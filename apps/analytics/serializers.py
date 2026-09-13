from rest_framework import serializers

from .models import StudentRecommendation


class StudentRecommendationSerializer(serializers.ModelSerializer):
    # Exposed as the Project's public_id (not its internal numeric pk), matching
    # the ?project=<public_id> convention used across sources/quizzes/study-plans.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = StudentRecommendation
        fields = (
            'id', 'project', 'subject', 'title', 'summary', 'overall_score', 'strengths', 'weaknesses',
            'recommendations', 'next_best_action', 'source_metrics', 'is_read', 'created_at', 'updated_at',
        )
        read_only_fields = fields
