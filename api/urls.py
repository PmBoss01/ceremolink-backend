from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import EventViewSet, RegisterView, public_event_view, me_view, google_auth_view, set_plan_preference
from .payments import create_checkout_session, stripe_webhook, confirm_payment_session
from .chat_views import start_conversation, get_messages, send_message, list_conversations

router = DefaultRouter()
router.register(r'events', EventViewSet, basename='event')

urlpatterns = [
    # Event CRUD (authenticated owners only)
    path('', include(router.urls)),

    # Auth endpoints
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/me/', me_view, name='me'),
    path('auth/google/', google_auth_view, name='google_auth'),
    path('auth/set-plan/', set_plan_preference, name='set_plan_preference'),

    # Public viewer endpoint (no login required)
    path('event/<slug:slug>/', public_event_view, name='public_event'),

    # Payment endpoints
    path('payments/create-checkout-session/', create_checkout_session, name='create_checkout_session'),
    path('payments/confirm-session/', confirm_payment_session, name='confirm_payment_session'),
    path('payments/webhook/', stripe_webhook, name='stripe_webhook'),

    # Chat support endpoints
    path('chat/start/', start_conversation, name='chat_start'),
    path('chat/<int:conversation_id>/messages/', get_messages, name='chat_get_messages'),
    path('chat/<int:conversation_id>/send/', send_message, name='chat_send_message'),
    path('chat/conversations/', list_conversations, name='chat_list_conversations'),
]
