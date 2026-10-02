from rest_framework import serializers

from apps.subjects.models import Subject

from .models import KnowledgeReply, KnowledgeThread


class KnowledgeReplySerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source='author.full_name', read_only=True)
    is_helpful_by_me = serializers.SerializerMethodField()
    is_accepted = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeReply
        fields = (
            'id', 'thread', 'author_name', 'is_teacher_reply', 'body', 'helpful_count', 'is_helpful_by_me',
            'is_accepted', 'created_at',
        )
        read_only_fields = (
            'id', 'author_name', 'is_teacher_reply', 'helpful_count', 'is_helpful_by_me', 'is_accepted', 'created_at',
        )

    def get_is_helpful_by_me(self, obj) -> bool:
        request = self.context.get('request')
        if not request:
            return False
        return obj.helpful_votes.filter(user=request.user).exists()

    def get_is_accepted(self, obj) -> bool:
        return obj.thread.accepted_reply_id == obj.id


class KnowledgeReplyCreateSerializer(serializers.Serializer):
    thread = serializers.IntegerField()
    body = serializers.CharField()


class KnowledgeThreadSerializer(serializers.ModelSerializer):
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True, default=None)
    author_name = serializers.CharField(source='author.full_name', read_only=True)
    reply_count = serializers.IntegerField(read_only=True, default=0)
    is_saved_by_me = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeThread
        fields = (
            'id', 'classroom', 'subject', 'subject_name', 'topic', 'author_name', 'is_teacher_content',
            'thread_type', 'title', 'body', 'status', 'accepted_reply', 'reply_count', 'is_saved_by_me', 'created_at',
        )
        read_only_fields = (
            'id', 'author_name', 'is_teacher_content', 'accepted_reply', 'reply_count', 'is_saved_by_me', 'created_at',
        )

    def get_is_saved_by_me(self, obj) -> bool:
        request = self.context.get('request')
        if not request:
            return False
        return obj.saved_by.filter(user=request.user).exists()


class KnowledgeThreadCreateSerializer(serializers.Serializer):
    classroom = serializers.UUIDField()
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True)
    topic = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    thread_type = serializers.ChoiceField(choices=KnowledgeThread.ThreadType.choices, default=KnowledgeThread.ThreadType.QUESTION)
    title = serializers.CharField(max_length=255)
    body = serializers.CharField(required=False, allow_blank=True, default='')


class KnowledgeThreadUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeThread
        fields = ('title', 'body', 'topic', 'status')


class AcceptReplySerializer(serializers.Serializer):
    reply = serializers.IntegerField()


class HelpfulToggleResponseSerializer(serializers.Serializer):
    is_helpful = serializers.BooleanField()
    helpful_count = serializers.IntegerField()


class SavedToggleResponseSerializer(serializers.Serializer):
    is_saved = serializers.BooleanField()
