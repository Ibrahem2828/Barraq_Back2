from rest_framework import serializers

from apps.subjects.models import Subject

from .models import MistakeEntry
from .scheduling import REVIEW_RESULTS


class MistakeEntrySerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source='subject.name', read_only=True, default=None)

    class Meta:
        model = MistakeEntry
        fields = (
            'id', 'subject', 'subject_name', 'topic', 'question_text', 'student_answer_text',
            'correct_answer_text', 'explanation', 'category', 'status', 'box', 'next_review_at',
            'review_count', 'last_reviewed_at', 'mastered_at', 'created_at',
        )
        read_only_fields = (
            'id', 'status', 'box', 'next_review_at', 'review_count', 'last_reviewed_at', 'mastered_at', 'created_at',
        )


class MistakeEntryCreateSerializer(serializers.ModelSerializer):
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )

    class Meta:
        model = MistakeEntry
        fields = (
            'subject', 'topic', 'question_text', 'student_answer_text', 'correct_answer_text',
            'explanation', 'category',
        )


class MistakeEntryUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MistakeEntry
        fields = ('category', 'explanation', 'topic')


class ImportFromAttemptSerializer(serializers.Serializer):
    attempt = serializers.IntegerField()


class ReviewResultSerializer(serializers.Serializer):
    result = serializers.ChoiceField(choices=[(value, value) for value in REVIEW_RESULTS])


class FlashcardReviewSerializer(serializers.Serializer):
    summary = serializers.IntegerField()
    flashcard_index = serializers.IntegerField(min_value=0)
    result = serializers.ChoiceField(choices=[(value, value) for value in REVIEW_RESULTS])


class DueFlashcardSerializer(serializers.Serializer):
    """Shapes a `due_flashcards()` row: no model backs a card by itself."""

    summary = serializers.IntegerField(source='summary.id')
    summary_title = serializers.CharField(source='summary.title')
    flashcard_index = serializers.IntegerField()
    card = serializers.DictField()
    next_review_at = serializers.DateTimeField(allow_null=True)


class ReviewQueueItemSerializer(serializers.Serializer):
    """One row of the merged mistake + flashcard review queue."""

    kind = serializers.ChoiceField(choices=[('mistake', 'mistake'), ('flashcard', 'flashcard')])
    mistake = MistakeEntrySerializer(allow_null=True)
    flashcard = DueFlashcardSerializer(allow_null=True)
