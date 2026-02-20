import datetime
import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import Event, EventOwnerProfile


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def create_checkout_session(request):
    """Creates a Stripe Checkout session for per-event payment or Pro subscription."""
    stripe.api_key = settings.STRIPE_SECRET_KEY

    checkout_type = request.data.get('type')  # 'per_event' | 'subscription'

    # ── Per-event payment ──────────────────────────────────────────────────────
    if checkout_type == 'per_event':
        event_id = request.data.get('event_id')  # Optional — omit for upfront credit purchase

        if event_id:
            # In-dashboard activation: pay for a specific existing draft event
            try:
                event = Event.objects.get(id=event_id, owner=request.user)
            except Event.DoesNotExist:
                return Response({'error': 'Event not found.'}, status=status.HTTP_404_NOT_FOUND)

            if event.is_paid:
                return Response({'error': 'This event is already activated.'}, status=status.HTTP_400_BAD_REQUEST)

            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                mode='payment',
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'CeremoLink Event: {event.title}',
                            'description': 'Full event activation — PDF upload, QR code generation & unlimited updates.',
                        },
                        'unit_amount': settings.STRIPE_PER_EVENT_PRICE,
                    },
                    'quantity': 1,
                }],
                metadata={
                    'type': 'per_event',
                    'event_id': str(event.id),
                    'user_id': str(request.user.id),
                },
                success_url=(
                    f"{settings.FRONTEND_URL}/dashboard/events/{event.id}"
                    f"?payment=success"
                ),
                cancel_url=f"{settings.FRONTEND_URL}/dashboard/events/{event.id}?payment=canceled",
            )
        else:
            # Upfront credit purchase: user pays first, event is created afterward from dashboard
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                mode='payment',
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': 'CeremoLink Event Activation',
                            'description': 'Single event activation — PDF upload, QR code generation & unlimited updates.',
                        },
                        'unit_amount': settings.STRIPE_PER_EVENT_PRICE,
                    },
                    'quantity': 1,
                }],
                metadata={
                    'type': 'per_event',
                    'user_id': str(request.user.id),
                    # No event_id — credit applied automatically when user creates their event
                },
                success_url=(
                    f"{settings.FRONTEND_URL}/dashboard/payment/success"
                    f"?session_id={{CHECKOUT_SESSION_ID}}&plan=per_event"
                ),
                cancel_url=f"{settings.FRONTEND_URL}/dashboard?payment=canceled",
            )
        return Response({'url': session.url})

    # ── Pro subscription ───────────────────────────────────────────────────────
    elif checkout_type == 'subscription':
        profile = request.user.profile

        if profile.plan == 'pro' and profile.subscription_status == 'active':
            return Response(
                {'error': 'You already have an active Pro subscription.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Accept 'month' or 'year' interval — defaults to monthly
        interval = request.data.get('interval', 'month')
        if interval not in ('month', 'year'):
            interval = 'month'

        price_amount = (
            settings.STRIPE_SUBSCRIPTION_YEARLY_PRICE
            if interval == 'year'
            else settings.STRIPE_SUBSCRIPTION_PRICE
        )

        # Find or create a Stripe Customer so the portal works later
        if profile.stripe_customer_id:
            customer_id = profile.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=request.user.email,
                metadata={'user_id': str(request.user.id)},
            )
            customer_id = customer.id
            EventOwnerProfile.objects.filter(user=request.user).update(
                stripe_customer_id=customer_id
            )

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            mode='subscription',
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'CeremoLink Pro',
                        'description': 'Unlimited events with full features — PDF upload, QR codes & priority support.',
                    },
                    'unit_amount': price_amount,
                    'recurring': {'interval': interval},
                },
                'quantity': 1,
            }],
            metadata={
                'type': 'subscription',
                'user_id': str(request.user.id),
                'interval': interval,
            },
            success_url=(
                f"{settings.FRONTEND_URL}/dashboard/payment/success"
                f"?session_id={{CHECKOUT_SESSION_ID}}&plan=pro"
            ),
            cancel_url=f"{settings.FRONTEND_URL}/dashboard?payment=canceled",
        )
        return Response({'url': session.url})

    return Response(
        {'error': 'Invalid type. Use "per_event" or "subscription".'},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def confirm_payment_session(request):
    """
    Called from the payment success page to verify a Stripe checkout session
    and apply the plan upgrade immediately — without depending on webhook timing.
    Only upgrades; never downgrades an already-correct plan.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session_id = request.data.get('session_id')

    if not session_id:
        return Response({'error': 'session_id required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        return Response({'error': 'Invalid session.'}, status=status.HTTP_400_BAD_REQUEST)

    # Only process fully paid sessions
    if session.get('payment_status') != 'paid':
        return Response({'error': 'Payment not completed.'}, status=status.HTTP_400_BAD_REQUEST)

    metadata = session.get('metadata', {})
    checkout_type = metadata.get('type')
    user_id = metadata.get('user_id')

    # Security: session must belong to this user
    if str(request.user.id) != str(user_id):
        return Response({'error': 'Unauthorized.'}, status=status.HTTP_403_FORBIDDEN)

    try:
        profile = request.user.profile
    except EventOwnerProfile.DoesNotExist:
        profile = EventOwnerProfile.objects.create(user=request.user)

    if checkout_type == 'subscription':
        subscription_id = session.get('subscription', '') or profile.stripe_subscription_id

        # Upgrade plan/interval only if not already on Pro
        if profile.plan != 'pro':
            profile.plan = 'pro'
            profile.subscription_status = 'active'
            profile.subscription_interval = metadata.get('interval', 'month')
            if subscription_id:
                profile.stripe_subscription_id = subscription_id

        # Always try to populate period_end when it's missing — the webhook may have
        # already set plan='pro' but skipped period_end if its retrieve failed.
        if not profile.subscription_current_period_end and subscription_id:
            try:
                sub = stripe.Subscription.retrieve(subscription_id)
                # Also backfill interval if somehow blank
                if not profile.subscription_interval:
                    profile.subscription_interval = sub['items']['data'][0]['plan']['interval']
                profile.subscription_current_period_end = datetime.datetime.fromtimestamp(
                    sub['current_period_end'], tz=datetime.timezone.utc
                )
            except Exception:
                pass

        profile.save()
        # Activate any existing unpaid events
        Event.objects.filter(owner=request.user, is_paid=False).update(is_paid=True)

    period_end = profile.subscription_current_period_end
    return Response({
        'plan': profile.plan,
        'subscription_interval': profile.subscription_interval,
        'subscription_period_end': period_end.isoformat() if period_end else None,
    })


@csrf_exempt
def stripe_webhook(request):
    """
    Stripe sends signed POST events here.
    Plain Django view (not DRF) so request.body is accessible before any parsing.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    stripe.api_key = settings.STRIPE_SECRET_KEY
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse('Invalid signature.', status=400)

    event_type = event['type']
    data = event['data']['object']

    # ── Checkout completed ─────────────────────────────────────────────────────
    if event_type == 'checkout.session.completed':
        metadata = data.get('metadata', {})
        checkout_type = metadata.get('type')

        if checkout_type == 'per_event':
            event_id = metadata.get('event_id')
            user_id = metadata.get('user_id')

            if event_id:
                # In-dashboard activation: mark specific event as paid
                try:
                    db_event = Event.objects.get(id=event_id)
                    db_event.is_paid = True
                    db_event.paid_per_event = True
                    db_event.save()
                except Event.DoesNotExist:
                    pass
            elif user_id:
                # Upfront credit purchase: add one credit to the user's profile
                try:
                    profile = EventOwnerProfile.objects.get(user_id=user_id)
                    profile.event_credits += 1
                    profile.save()
                except EventOwnerProfile.DoesNotExist:
                    pass

        elif checkout_type == 'subscription':
            user_id = metadata.get('user_id')
            subscription_id = data.get('subscription', '')
            try:
                profile = EventOwnerProfile.objects.get(user_id=user_id)
                profile.plan = 'pro'
                profile.stripe_subscription_id = subscription_id
                profile.subscription_status = 'active'
                # Interval is stored in metadata at checkout creation — no API call needed
                profile.subscription_interval = metadata.get('interval', 'month')

                # Best-effort: retrieve subscription for period_end (non-critical)
                if subscription_id:
                    try:
                        sub = stripe.Subscription.retrieve(subscription_id)
                        profile.subscription_current_period_end = datetime.datetime.fromtimestamp(
                            sub['current_period_end'], tz=datetime.timezone.utc
                        )
                    except Exception:
                        pass  # period_end populated on customer.subscription.updated webhook

                profile.save()
                # Activate all existing draft events for this subscriber
                Event.objects.filter(owner_id=user_id, is_paid=False).update(is_paid=True)
            except EventOwnerProfile.DoesNotExist:
                pass

    # ── Subscription cancelled ─────────────────────────────────────────────────
    elif event_type == 'customer.subscription.deleted':
        subscription_id = data.get('id', '')
        try:
            profile = EventOwnerProfile.objects.get(stripe_subscription_id=subscription_id)
            profile.plan = 'free'
            profile.subscription_status = 'canceled'
            profile.save()
        except EventOwnerProfile.DoesNotExist:
            pass

    # ── Subscription updated (payment failure, renewal, etc.) ──────────────────
    elif event_type == 'customer.subscription.updated':
        subscription_id = data.get('id', '')
        sub_status = data.get('status', '')
        try:
            profile = EventOwnerProfile.objects.get(stripe_subscription_id=subscription_id)
            profile.subscription_status = sub_status
            profile.plan = 'pro' if sub_status in ('active', 'trialing') else 'free'

            # Update billing interval and renewal date on every subscription change
            items = data.get('items', {}).get('data', [])
            if items:
                profile.subscription_interval = items[0].get('plan', {}).get('interval', '')
            period_end = data.get('current_period_end')
            if period_end:
                profile.subscription_current_period_end = datetime.datetime.fromtimestamp(
                    period_end, tz=datetime.timezone.utc
                )

            profile.save()
        except EventOwnerProfile.DoesNotExist:
            pass

    return HttpResponse(status=200)
