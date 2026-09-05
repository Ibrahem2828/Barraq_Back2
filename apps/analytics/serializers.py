from rest_framework import serializers

from .models import StudentRecommendation


class StudentRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentRecommendation
        fields = (
            'id', 'subject', 'title', 'summary', 'overall_score', 'strengths', 'weaknesses',
            'recommendations', 'next_best_action', 'source_metrics', 'is_read', 'created_at', 'updated_at',
        )
        read_only_fields = fields
