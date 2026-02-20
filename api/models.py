from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class EventOwnerProfile(models.Model):
    PLAN_CHOICES = [('free', 'Free'), ('per_event', 'Pay Per Event'), ('pro', 'Pro')]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    stripe_customer_id = models.CharField(max_length=100, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    subscription_status = models.CharField(max_length=50, blank=True)
    subscription_interval = models.CharField(max_length=10, blank=True)  # 'month' or 'year'
    subscription_current_period_end = models.DateTimeField(null=True, blank=True)
    # Lifetime counter — never decremented on delete, prevents delete-and-recreate abuse
    total_events_created = models.PositiveIntegerField(default=0)
    # Pre-purchased per-event credits (paid upfront, consumed on event creation)
    event_credits = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.user.username} ({self.plan})"


class Event(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=250, unique=True, blank=True)
    description = models.TextField(blank=True)
    content = models.TextField(blank=True)
    pdf_file = models.FileField(upload_to='pdfs/', blank=True, null=True)
    cover_image = models.ImageField(upload_to='covers/', blank=True, null=True)
    qr_code = models.ImageField(upload_to='qrcodes/', blank=True, null=True)
    is_published = models.BooleanField(default=False)
    is_paid = models.BooleanField(default=False)
    paid_per_event = models.BooleanField(default=False)  # True if activated via per-event payment (no expiry)
    view_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def _generate_unique_slug(self):
        base_slug = slugify(self.title)
        slug = base_slug
        counter = 1
        while Event.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug

    def _generate_qr_code(self):
        import qrcode
        from io import BytesIO
        from django.core.files.base import ContentFile
        from django.conf import settings

        public_url = f"{settings.FRONTEND_URL}/event/{self.slug}"
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(public_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')

        buffer = BytesIO()
        img.save(buffer, format='PNG')

        filename = f"qr_{self.slug}.png"
        # save=False writes the file to storage without triggering model.save() again
        self.qr_code.save(filename, ContentFile(buffer.getvalue()), save=False)
        Event.objects.filter(pk=self.pk).update(qr_code=self.qr_code.name)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()

        # Determine if QR should be generated after saving
        generate_qr = self.is_published and self.is_paid and not self.qr_code

        super().save(*args, **kwargs)

        if generate_qr:
            self._generate_qr_code()

    def __str__(self):
        return self.title


class ChatConversation(models.Model):
    """One support conversation — either from an authenticated user or an anonymous guest."""
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='chat_conversations'
    )
    guest_email = models.EmailField(blank=True)
    token = models.CharField(max_length=64, unique=True)  # guest session token
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Chat: {self.user or self.guest_email} ({self.created_at.date()})"


class ChatMessage(models.Model):
    SENDER_CHOICES = [('user', 'User'), ('admin', 'Admin')]
    conversation = models.ForeignKey(ChatConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.sender}] {self.body[:60]}"
