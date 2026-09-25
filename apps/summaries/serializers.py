from rest_framework import serializers

from .models import Summary


class SummarySerializer(serializers.ModelSerializer):
    # Exposed as the Project's public_id (not its internal numeric pk), matching
    # the ?project=<public_id> convention used across sources/quizzes/study-plans.
    project: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = Summary
        fields = (
            'id', 'project', 'source', 'collection', 'title', 'short_summary', 'detailed_summary',
            'key_points', 'important_terms', 'covered_topics', 'review_questions',
            'flashcards',
            'source_references', 'quality_score', 'created_at', 'updated_at',
        )
        read_only_fields = fields
