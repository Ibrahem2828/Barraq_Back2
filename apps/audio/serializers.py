from rest_framework import serializers

from .models import Transcription


class TranscriptionSerializer(serializers.ModelSerializer):
    # Exposed as the Project's public_id (not its internal numeric pk), matching
    # the ?project=<public_id> convention used across sources/quizzes/study-plans.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = Transcription
        fields = (
            'id', 'project', 'source', 'title', 'language', 'full_transcript', 'cleaned_transcript',
            'segments', 'detected_topics', 'duration_seconds', 'confidence_score',
            'created_at', 'updated_at',
        )
        read_only_fields = fields
