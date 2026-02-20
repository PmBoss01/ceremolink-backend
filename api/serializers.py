from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Event, EventOwnerProfile


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
        )
        EventOwnerProfile.objects.create(user=user)
        return user


class EventSerializer(serializers.ModelSerializer):
    owner = UserSerializer(read_only=True)

    class Meta:
        model = Event
        fields = '__all__'
        read_only_fields = ['slug', 'qr_code', 'view_count', 'created_at', 'updated_at', 'is_paid', 'published_at']


class PublicEventSerializer(serializers.ModelSerializer):
    """Minimal serializer for public viewers — excludes owner details."""
    class Meta:
        model = Event
        fields = [
            'title', 'slug', 'description', 'content',
            'pdf_file', 'cover_image', 'view_count', 'created_at',
        ]
