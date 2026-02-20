from django.db import models as db_models
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import viewsets, permissions, generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Event, EventOwnerProfile
from .serializers import EventSerializer, PublicEventSerializer, RegisterSerializer, UserSerializer


def _get_or_create_google_user(email, name):
    """Find existing user by email or create a new one from Google profile."""
    try:
        return User.objects.get(email=email), False
    except User.DoesNotExist:
        pass

    # Build a unique username from the email prefix
    base = email.split('@')[0].replace('.', '_').lower()
    username, counter = base, 1
    while User.objects.filter(username=username).exists():
        username = f"{base}{counter}"
        counter += 1

    user = User.objects.create_user(username=username, email=email, password=None)

    # Split display name into first/last
    parts = name.strip().split(' ', 1) if name else []
    user.first_name = parts[0] if parts else ''
    user.last_name = parts[1] if len(parts) > 1 else ''
    user.save()

    EventOwnerProfile.objects.create(user=user)
    return user, True


class RegisterView(generics.CreateAPIView):
    """Public endpoint — anyone can register as an Event Owner."""
    permission_classes = [permissions.AllowAny]
    serializer_class = RegisterSerializer


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def me_view(request):
    """Returns the currently authenticated user's info including plan details."""
    user = request.user
    try:
        profile = user.profile
    except EventOwnerProfile.DoesNotExist:
        profile = EventOwnerProfile.objects.create(user=user)

    period_end = profile.subscription_current_period_end
    return Response({
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'plan': profile.plan,
        'subscription_status': profile.subscription_status,
        'subscription_interval': profile.subscription_interval,
        'subscription_period_end': period_end.isoformat() if period_end else None,
        'event_count': user.events.count(),
        'events_used': profile.total_events_created,
        'event_credits': profile.event_credits,
        'has_per_event_events': user.events.filter(paid_per_event=True).exists(),
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def google_auth_view(request):
    """
    Accepts a Google access token from the frontend,
    verifies it via Google's userinfo endpoint, finds or creates a Django user,
    and returns JWT tokens.
    """
    import json
    import urllib.request
    import urllib.error

    access_token = request.data.get('access_token')
    if not access_token:
        return Response({'error': 'access_token is required.'}, status=status.HTTP_400_BAD_REQUEST)

    req = urllib.request.Request(
        'https://www.googleapis.com/oauth2/v3/userinfo',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read().decode())
    except (urllib.error.URLError, Exception):
        return Response({'error': 'Invalid Google token.'}, status=status.HTTP_400_BAD_REQUEST)

    email = info.get('email')
    if not email:
        return Response({'error': 'Email not provided by Google.'}, status=status.HTTP_400_BAD_REQUEST)

    if not info.get('email_verified'):
        return Response({'error': 'Google email not verified.'}, status=status.HTTP_400_BAD_REQUEST)

    name = info.get('name', '')
    user, _ = _get_or_create_google_user(email, name)

    refresh = RefreshToken.for_user(user)
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    })


class EventViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for events. Only the authenticated owner sees their own events.
    """
    serializer_class = EventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Event.objects.filter(owner=self.request.user)

    def _get_profile(self):
        try:
            return self.request.user.profile
        except EventOwnerProfile.DoesNotExist:
            return EventOwnerProfile.objects.create(user=self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        profile = self._get_profile()

        # Free plan: 1 lifetime event limit (prevents delete-and-recreate abuse)
        if profile.plan == 'free' and profile.total_events_created >= 1:
            raise PermissionDenied(
                'You are on the free plan which allows only 1 event. '
                'You have already used your free event. '
                'Upgrade to Pro or pay per event to create more.'
            )

        # Free plan: strip PDF (premium feature)
        if profile.plan == 'free':
            serializer.validated_data.pop('pdf_file', None)

        # Save with plan-appropriate activation state
        if profile.plan == 'pro':
            # Pro: auto-publish immediately — subscription period end is the expiry
            instance = serializer.save(owner=user, is_paid=True, is_published=True)
            Event.objects.filter(pk=instance.pk).update(published_at=timezone.now())
        elif profile.plan == 'free':
            # Free: auto-publish immediately — 3-hour window starts now
            instance = serializer.save(owner=user, is_published=True, is_paid=True)
            Event.objects.filter(pk=instance.pk).update(published_at=timezone.now())
        else:
            # Per event: create as unpaid draft — user pays separately to publish
            instance = serializer.save(owner=user)

        # Increment lifetime counter (never decremented on delete)
        EventOwnerProfile.objects.filter(user=user).update(
            total_events_created=profile.total_events_created + 1
        )

    def perform_update(self, serializer):
        profile = self._get_profile()

        # Free plan: strip PDF on update too
        if profile.plan == 'free':
            serializer.validated_data.pop('pdf_file', None)
            # Lock is_published to True only for genuinely free events.
            # Per-event paid events (paid_per_event=True) on a free-plan account
            # should still allow the owner to toggle publish state.
            if not serializer.instance.paid_per_event:
                serializer.validated_data['is_published'] = True
        elif (
            profile.plan != 'pro'
            and not serializer.instance.paid_per_event
            and serializer.instance.is_paid
        ):
            # Old free event on an upgraded (per_event) account: keep locked live.
            # The 3-hour backend expiry still applies — the owner cannot manually
            # unpublish to extend or reset the countdown window.
            serializer.validated_data['is_published'] = True

        was_published = serializer.instance.is_published
        instance = serializer.save()

        # Record when the event was first published (used for 24h free plan expiry)
        if not was_published and instance.is_published and not instance.published_at:
            Event.objects.filter(pk=instance.pk).update(published_at=timezone.now())


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def set_plan_preference(request):
    """
    Sets a user's plan to 'per_event' after they choose that plan during sign-up/login.
    Only upgrades from free — never downgrades an existing paid plan.
    """
    plan = request.data.get('plan')
    if plan not in ['per_event']:
        return Response({'error': 'Invalid plan.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        profile = request.user.profile
    except EventOwnerProfile.DoesNotExist:
        profile = EventOwnerProfile.objects.create(user=request.user)

    if profile.plan == 'free':
        profile.plan = plan
        profile.save(update_fields=['plan'])

    return Response({'plan': profile.plan})


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def public_event_view(request, slug):
    """
    Public endpoint for QR code scanners.
    No login required. Returns event details and increments view count.
    Only published and paid events are accessible.
    """
    try:
        event = Event.objects.get(slug=slug, is_published=True, is_paid=True)
    except Event.DoesNotExist:
        return Response(
            {'error': 'Event not found or is not available.', 'code': 'not_available'},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        owner_plan = event.owner.profile.plan
    except EventOwnerProfile.DoesNotExist:
        owner_plan = 'free'

    if owner_plan == 'pro':
        # Pro: no expiry — subscription covers unlimited access
        pass
    elif event.paid_per_event and event.published_at:
        # Pay per event activation: 24-hour publish window
        elapsed_seconds = (timezone.now() - event.published_at).total_seconds()
        if elapsed_seconds > 86400:  # 24 hours
            return Response(
                {'error': 'Event expired.', 'code': 'expired_per_event'},
                status=status.HTTP_404_NOT_FOUND
            )
    elif event.published_at:
        # All other events (created under free plan, regardless of owner's current plan):
        # 3-hour publish window — plan upgrades do not extend this window.
        elapsed_seconds = (timezone.now() - event.published_at).total_seconds()
        if elapsed_seconds > 10800:  # 3 hours
            return Response(
                {'error': 'Event expired.', 'code': 'expired_free'},
                status=status.HTTP_404_NOT_FOUND
            )

    # Increment view count atomically to handle concurrent requests safely
    Event.objects.filter(pk=event.pk).update(
        view_count=db_models.F('view_count') + 1
    )
    event.refresh_from_db()

    serializer = PublicEventSerializer(event, context={'request': request})
    return Response(serializer.data)
