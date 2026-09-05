from rest_framework import serializers

from .models import Summary


class SummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Summary
        fields = (
            'id', 'source', 'collection', 'title', 'short_summary', 'detailed_summary',
            'key_points', 'important_terms', 'covered_topics', 'review_questions',
            'source_references', 'quality_score', 'created_at', 'updated_at',
        )
        read_only_fields = fields
