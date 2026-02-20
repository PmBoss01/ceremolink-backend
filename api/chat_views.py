import secrets

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from .models import ChatConversation, ChatMessage


def _resolve_conversation(request, conversation_id):
    """
    Returns (conversation, None) if the caller is authorised,
    or (None, error_Response) if not.
    Guests must supply ?token= or X-Chat-Token header.
    Auth users must own the conversation (or be staff).
    """
    try:
        conversation = ChatConversation.objects.get(id=conversation_id)
    except ChatConversation.DoesNotExist:
        return None, Response({'error': 'Conversation not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.user.is_authenticated:
        if not request.user.is_staff and conversation.user != request.user:
            return None, Response({'error': 'Unauthorized.'}, status=status.HTTP_403_FORBIDDEN)
    else:
        token = (
            request.data.get('token')
            or request.query_params.get('token')
            or request.META.get('HTTP_X_CHAT_TOKEN', '')
        )
        if conversation.token != token:
            return None, Response({'error': 'Unauthorized.'}, status=status.HTTP_403_FORBIDDEN)

    return conversation, None


@api_view(['POST'])
@permission_classes([AllowAny])
def start_conversation(request):
    """
    Start a new chat conversation.
    Authenticated users: identity taken from JWT.
    Guests: must supply { email, message? }.
    """
    if request.user.is_authenticated:
        conversation = ChatConversation.objects.create(
            user=request.user,
            token=secrets.token_urlsafe(32),
        )
    else:
        email = request.data.get('email', '').strip()
        if not email:
            return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)
        conversation = ChatConversation.objects.create(
            guest_email=email,
            token=secrets.token_urlsafe(32),
        )

    # Optionally include the first message in the same request
    first_message = request.data.get('message', '').strip()
    if first_message:
        ChatMessage.objects.create(
            conversation=conversation,
            sender='user',
            body=first_message,
        )

    return Response({'id': conversation.id, 'token': conversation.token})


@api_view(['GET'])
@permission_classes([AllowAny])
def get_messages(request, conversation_id):
    """Return all messages for a conversation."""
    conversation, error = _resolve_conversation(request, conversation_id)
    if error:
        return error

    messages = list(conversation.messages.values('id', 'sender', 'body', 'created_at'))
    return Response(messages)


@api_view(['POST'])
@permission_classes([AllowAny])
def send_message(request, conversation_id):
    """Append a message to a conversation."""
    conversation, error = _resolve_conversation(request, conversation_id)
    if error:
        return error

    body = request.data.get('body', '').strip()
    if not body:
        return Response({'error': 'Message cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)

    sender = 'admin' if (request.user.is_authenticated and request.user.is_staff) else 'user'
    message = ChatMessage.objects.create(conversation=conversation, sender=sender, body=body)

    return Response({
        'id': message.id,
        'sender': message.sender,
        'body': message.body,
        'created_at': message.created_at.isoformat(),
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def list_conversations(request):
    """Admin: list all conversations with their latest message."""
    convs = (
        ChatConversation.objects
        .order_by('-created_at')
        .values('id', 'user__username', 'user__email', 'guest_email', 'created_at')
    )
    return Response(list(convs))
