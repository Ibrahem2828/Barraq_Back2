from rest_framework import serializers

from apps.sources.models import StudentSource

from .models import GroupGoal, GroupMembership, GroupMessage, GroupSession, GroupSessionSummary, GroupTask, StudyGroup


class StudyGroupSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True, default=0)
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = StudyGroup
        fields = ('id', 'name', 'invite_code', 'max_members', 'is_active', 'member_count', 'my_role', 'created_at')
        read_only_fields = ('id', 'invite_code', 'is_active', 'member_count', 'created_at')

    def get_my_role(self, obj) -> str | None:
        request = self.context.get('request')
        if not request:
            return None
        membership = GroupMembership.objects.filter(
            group=obj, user=request.user, status=GroupMembership.Status.ACTIVE
        ).first()
        return membership.role if membership else None


class StudyGroupCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    max_members = serializers.IntegerField(min_value=2, max_value=50, default=8)


class JoinGroupSerializer(serializers.Serializer):
    invite_code = serializers.CharField(max_length=8)


class GroupMembershipSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = GroupMembership
        fields = ('id', 'user', 'user_name', 'user_email', 'role', 'status', 'joined_at')
        read_only_fields = fields


class BanMemberSerializer(serializers.Serializer):
    user = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class GroupGoalSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupGoal
        fields = ('id', 'title', 'target_date', 'is_done', 'created_at')
        read_only_fields = ('id', 'created_at')


class GroupTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupTask
        fields = ('id', 'title', 'is_done', 'assigned_to', 'order', 'created_at')
        read_only_fields = ('id', 'created_at')


class GroupSessionSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupSessionSummary
        fields = ('tasks_completed_count', 'duration_actual_seconds')
        read_only_fields = fields


class GroupSessionSerializer(serializers.ModelSerializer):
    summary = GroupSessionSummarySerializer(read_only=True)

    class Meta:
        model = GroupSession
        fields = (
            'id', 'scheduled_at', 'duration_minutes', 'started_at', 'ended_at', 'status', 'summary', 'created_at',
        )
        read_only_fields = ('id', 'started_at', 'ended_at', 'status', 'summary', 'created_at')


class GroupSessionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupSession
        fields = ('scheduled_at', 'duration_minutes')


class SessionStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    remaining_seconds = serializers.IntegerField()


class GroupMessageSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    attached_source_title = serializers.CharField(source='attached_source.title', read_only=True, default=None)

    class Meta:
        model = GroupMessage
        fields = ('id', 'user', 'user_name', 'body', 'attached_source', 'attached_source_title', 'is_flagged', 'created_at')
        read_only_fields = ('id', 'user', 'user_name', 'attached_source_title', 'is_flagged', 'created_at')


class GroupMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(required=False, allow_blank=True, default='')
    attached_source = serializers.PrimaryKeyRelatedField(
        queryset=StudentSource.objects.all(), required=False, allow_null=True
    )

    def validate_attached_source(self, value):
        if value and value.user_id != self.context['request'].user.id:
            raise serializers.ValidationError('Source not found.')
        return value

    def validate(self, attrs):
        if not attrs.get('body') and not attrs.get('attached_source'):
            raise serializers.ValidationError('A message needs text or an attached source.')
        return attrs


class ReportMessageSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default='')
