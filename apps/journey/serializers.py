from rest_framework import serializers

from .models import ChallengeProgress, ClassGoal, PathStation, SubjectPath, Unlockable, WeeklyChallenge


class StationStateSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='station.id')
    title = serializers.CharField(source='station.title')
    order = serializers.IntegerField(source='station.order')
    gem_reward = serializers.IntegerField(source='station.gem_reward')
    completed = serializers.BooleanField()
    unlocked = serializers.BooleanField()


class SubjectJourneySerializer(serializers.Serializer):
    subject = serializers.IntegerField(source='subject.id')
    subject_name = serializers.CharField(source='subject.name')
    gem_fragments_total = serializers.IntegerField()
    stations = StationStateSerializer(many=True)


class UnlockableSerializer(serializers.ModelSerializer):
    unlocked = serializers.BooleanField(read_only=True, default=False)

    class Meta:
        model = Unlockable
        fields = ('id', 'code', 'name', 'category', 'gem_requirement', 'unlocked')
        read_only_fields = fields


class ChallengeProgressInlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChallengeProgress
        fields = ('progress_value', 'is_completed', 'completed_at')
        read_only_fields = fields


class WeeklyChallengeSerializer(serializers.ModelSerializer):
    my_progress = serializers.SerializerMethodField()

    class Meta:
        model = WeeklyChallenge
        fields = ('id', 'code', 'title', 'description', 'week_start', 'target_value', 'gem_reward', 'my_progress')
        read_only_fields = fields

    def get_my_progress(self, obj) -> dict | None:
        request = self.context.get('request')
        if not request:
            return None
        row = ChallengeProgress.objects.filter(user=request.user, challenge=obj).first()
        return ChallengeProgressInlineSerializer(row).data if row else {'progress_value': 0, 'is_completed': False, 'completed_at': None}


class JourneyHomeSerializer(serializers.Serializer):
    total_gems = serializers.IntegerField()
    subjects = SubjectJourneySerializer(many=True)


# -- content authoring (admin: journey.manage) -------------------------------
class PathStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PathStation
        fields = ('id', 'title', 'order', 'gem_reward', 'is_active')
        read_only_fields = ('id',)


class SubjectPathSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source='subject.name', read_only=True)
    stations = PathStationSerializer(many=True, read_only=True)

    class Meta:
        model = SubjectPath
        fields = ('id', 'subject', 'subject_name', 'is_active', 'stations')
        read_only_fields = ('id', 'subject_name', 'stations')


class PathStationWriteSerializer(serializers.ModelSerializer):
    path = serializers.PrimaryKeyRelatedField(queryset=SubjectPath.objects.all())

    class Meta:
        model = PathStation
        fields = ('path', 'title', 'order', 'gem_reward', 'is_active')


class UnlockableWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unlockable
        fields = ('code', 'name', 'category', 'gem_requirement', 'is_active')


class WeeklyChallengeWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklyChallenge
        fields = ('code', 'title', 'description', 'week_start', 'target_value', 'gem_reward', 'is_active')


# -- class goals (class_work.view / class_work.manage) -----------------------
class ClassGoalSerializer(serializers.ModelSerializer):
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = ClassGoal
        fields = ('id', 'classroom', 'title', 'target_value', 'progress_value', 'is_completed', 'created_at')
        read_only_fields = ('id', 'created_at')


class ClassGoalWriteSerializer(serializers.ModelSerializer):
    classroom = serializers.UUIDField(write_only=True)

    class Meta:
        model = ClassGoal
        fields = ('classroom', 'title', 'target_value', 'progress_value', 'is_completed')
