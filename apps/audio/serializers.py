from rest_framework import serializers

from .models import Transcription


class TranscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transcription
        fields = (
            'id', 'source', 'title', 'language', 'full_transcript', 'cleaned_transcript',
            'segments', 'detected_topics', 'duration_seconds', 'confidence_score',
            'created_at', 'updated_at',
        )
        read_only_fields = fields
