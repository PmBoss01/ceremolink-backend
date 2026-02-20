from django.contrib import admin
from .models import Event, EventOwnerProfile, ChatConversation, ChatMessage


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'owner', 'is_published', 'is_paid', 'view_count', 'created_at']
    list_filter = ['is_published', 'is_paid']
    search_fields = ['title', 'owner__username', 'owner__email']
    readonly_fields = ['slug', 'qr_code', 'view_count', 'created_at', 'updated_at']


@admin.register(EventOwnerProfile)
class EventOwnerProfileAdmin(admin.ModelAdmin):
    list_display = ['user']
    search_fields = ['user__username', 'user__email']


@admin.register(ChatConversation)
class ChatConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'guest_email', 'created_at']
    search_fields = ['user__username', 'user__email', 'guest_email']
    list_filter = ['created_at']
    readonly_fields = ['token', 'created_at']


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'sender', 'body', 'created_at']
    list_filter = ['sender', 'created_at']
    search_fields = ['body', 'conversation__user__username', 'conversation__guest_email']
    readonly_fields = ['created_at']
