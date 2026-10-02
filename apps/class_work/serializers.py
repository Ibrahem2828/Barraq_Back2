from django.db import transaction
from rest_framework import serializers

from apps.sources.validators import validate_student_source_file
from apps.subjects.models import Subject

from .models import (
    AssignmentSubmission,
    ClassAnnouncement,
    ClassAssignment,
    ClassEvent,
    ClassQuiz,
    ClassQuizAnswer,
    ClassQuizAttempt,
    ClassQuizChoice,
    ClassQuizQuestion,
)


# -- announcements -------------------------------------------------------------
class ClassAnnouncementSerializer(serializers.ModelSerializer):
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True, default=None)

    class Meta:
        model = ClassAnnouncement
        fields = ('id', 'classroom', 'title', 'body', 'pinned', 'created_by_name', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')


class ClassAnnouncementWriteSerializer(serializers.ModelSerializer):
    classroom = serializers.UUIDField(write_only=True)

    class Meta:
        model = ClassAnnouncement
        fields = ('classroom', 'title', 'body', 'pinned')


# -- calendar -------------------------------------------------------------------
class ClassEventSerializer(serializers.ModelSerializer):
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)

    class Meta:
        model = ClassEvent
        fields = ('id', 'classroom', 'title', 'event_type', 'description', 'start_at', 'end_at', 'created_at')
        read_only_fields = ('id', 'created_at')


class ClassEventWriteSerializer(serializers.ModelSerializer):
    classroom = serializers.UUIDField(write_only=True)

    class Meta:
        model = ClassEvent
        fields = ('classroom', 'title', 'event_type', 'description', 'start_at', 'end_at')

    def validate(self, attrs):
        end_at = attrs.get('end_at', getattr(self.instance, 'end_at', None))
        start_at = attrs.get('start_at', getattr(self.instance, 'start_at', None))
        if end_at and start_at and end_at < start_at:
            raise serializers.ValidationError({'end_at': 'End must not be before start.'})
        return attrs


# -- assignments ----------------------------------------------------------------
class ClassAssignmentSerializer(serializers.ModelSerializer):
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True, default=None)
    submissions_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ClassAssignment
        fields = (
            'id', 'classroom', 'subject', 'subject_name', 'title', 'instructions', 'due_at', 'allow_late',
            'max_points', 'status', 'submissions_count', 'created_at',
        )
        read_only_fields = ('id', 'created_at')


class ClassAssignmentWriteSerializer(serializers.ModelSerializer):
    classroom = serializers.UUIDField(write_only=True)
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )

    class Meta:
        model = ClassAssignment
        fields = ('classroom', 'subject', 'title', 'instructions', 'due_at', 'allow_late', 'max_points', 'status')


class AssignmentSubmissionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source='student.full_name', read_only=True)
    student_email = serializers.EmailField(source='student.email', read_only=True)

    class Meta:
        model = AssignmentSubmission
        fields = (
            'id', 'assignment', 'student_name', 'student_email', 'text_response', 'original_filename', 'file_size',
            'submitted_at', 'status', 'grade', 'feedback', 'graded_at',
        )
        read_only_fields = fields


class AssignmentSubmitSerializer(serializers.Serializer):
    text_response = serializers.CharField(required=False, allow_blank=True, default='')
    file = serializers.FileField(required=False, allow_null=True)

    def validate_file(self, value):
        if value:
            self.context['file_metadata'] = validate_student_source_file(value)
        return value

    def validate(self, attrs):
        if not attrs.get('text_response') and not attrs.get('file'):
            raise serializers.ValidationError('A text response or a file is required.')
        return attrs


class AssignmentGradeSerializer(serializers.Serializer):
    grade = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=0)
    feedback = serializers.CharField(required=False, allow_blank=True, default='')


# -- class quizzes: authoring ----------------------------------------------------
class ClassQuizChoiceManageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassQuizChoice
        fields = ('text', 'is_correct', 'order')


class ClassQuizQuestionSerializer(serializers.ModelSerializer):
    """Teacher-facing: includes which choice is correct."""

    choices = ClassQuizChoiceManageSerializer(many=True, read_only=True)

    class Meta:
        model = ClassQuizQuestion
        fields = ('id', 'question_type', 'text', 'explanation', 'points', 'order', 'choices')
        read_only_fields = ('id',)


class ClassQuizQuestionManageSerializer(serializers.ModelSerializer):
    class_quiz = serializers.PrimaryKeyRelatedField(queryset=ClassQuiz.objects.all())
    choices = ClassQuizChoiceManageSerializer(many=True, required=False)

    class Meta:
        model = ClassQuizQuestion
        fields = ('class_quiz', 'question_type', 'text', 'explanation', 'points', 'order', 'choices')

    def validate_class_quiz(self, value):
        # Re-resolved against the caller's scope on every write, the same
        # rule as the Classroom Shared Library: naming an id is not the
        # same as being allowed to reach it.
        from apps.organizations import scope as scope_policy

        from .services import MANAGE_PERMISSION

        try:
            scope_policy.assert_classroom_allowed(self.context['request'].user, value.classroom, MANAGE_PERMISSION)
        except scope_policy.ScopeDenied as exc:
            raise serializers.ValidationError('Quiz not found.') from exc
        if value.status == ClassQuiz.Status.PUBLISHED:
            raise serializers.ValidationError('A published quiz can no longer have its questions edited.')
        return value

    def validate(self, attrs):
        question_type = attrs.get('question_type', getattr(self.instance, 'question_type', ClassQuizQuestion.QuestionType.MCQ))
        choices = attrs.get('choices')
        if question_type == ClassQuizQuestion.QuestionType.WRITTEN:
            if choices:
                raise serializers.ValidationError({'choices': 'A written question has no choices.'})
        elif choices is not None:
            if len(choices) < 2:
                raise serializers.ValidationError({'choices': 'At least two choices are required.'})
            if sum(1 for item in choices if item.get('is_correct')) != 1:
                raise serializers.ValidationError({'choices': 'Exactly one choice must be correct.'})
        return attrs

    @staticmethod
    def _replace_choices(question, choices):
        question.choices.all().delete()
        ClassQuizChoice.objects.bulk_create(
            [
                ClassQuizChoice(question=question, text=item['text'], is_correct=item.get('is_correct', False), order=index)
                for index, item in enumerate(choices, start=1)
            ]
        )

    def create(self, validated_data):
        choices = validated_data.pop('choices', [])
        with transaction.atomic():
            question = ClassQuizQuestion.objects.create(**validated_data)
            self._replace_choices(question, choices)
        return question

    def update(self, instance, validated_data):
        choices = validated_data.pop('choices', None)
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()
            if choices is not None:
                self._replace_choices(instance, choices)
        return instance


class ClassQuizSerializer(serializers.ModelSerializer):
    """Teacher-facing detail: full questions with correct answers."""

    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True, default=None)
    questions = ClassQuizQuestionSerializer(many=True, read_only=True)
    questions_count = serializers.IntegerField(source='questions.count', read_only=True)

    class Meta:
        model = ClassQuiz
        fields = (
            'id', 'classroom', 'subject', 'subject_name', 'title', 'description', 'time_limit_minutes', 'due_at',
            'status', 'questions', 'questions_count', 'created_at',
        )
        read_only_fields = ('id', 'created_at')


class ClassQuizWriteSerializer(serializers.ModelSerializer):
    classroom = serializers.UUIDField(write_only=True)
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )

    class Meta:
        model = ClassQuiz
        fields = ('classroom', 'subject', 'title', 'description', 'time_limit_minutes', 'due_at', 'status')


# -- class quizzes: taking them ---------------------------------------------------
class ClassQuizStudentQuestionSerializer(serializers.ModelSerializer):
    """Student-facing while taking a quiz: never the correct choice."""

    choices = serializers.SerializerMethodField()

    class Meta:
        model = ClassQuizQuestion
        fields = ('id', 'question_type', 'text', 'points', 'order', 'choices')

    def get_choices(self, obj):
        return [{'id': choice.id, 'text': choice.text} for choice in obj.choices.all()]


class ClassQuizStudentSerializer(serializers.ModelSerializer):
    classroom: serializers.SlugRelatedField = serializers.SlugRelatedField(slug_field='public_id', read_only=True)
    subject_name = serializers.CharField(source='subject.name', read_only=True, default=None)
    questions_count = serializers.IntegerField(source='questions.count', read_only=True)

    class Meta:
        model = ClassQuiz
        fields = (
            'id', 'classroom', 'subject', 'subject_name', 'title', 'description', 'time_limit_minutes', 'due_at',
            'questions_count',
        )
        read_only_fields = fields


class ClassQuizAttemptSerializer(serializers.ModelSerializer):
    class_quiz_title = serializers.CharField(source='class_quiz.title', read_only=True)

    class Meta:
        model = ClassQuizAttempt
        fields = (
            'id', 'class_quiz', 'class_quiz_title', 'status', 'started_at', 'submitted_at', 'score', 'max_score',
            'percentage', 'fully_graded',
        )
        read_only_fields = fields


class ClassQuizAnswerSubmitSerializer(serializers.Serializer):
    question = serializers.IntegerField()
    selected_choice = serializers.IntegerField(required=False, allow_null=True)
    text_answer = serializers.CharField(required=False, allow_blank=True, default='')


class ClassQuizAnswerResultSerializer(serializers.ModelSerializer):
    question_text = serializers.CharField(source='question.text', read_only=True)
    question_type = serializers.CharField(source='question.question_type', read_only=True)
    explanation = serializers.CharField(source='question.explanation', read_only=True)

    class Meta:
        model = ClassQuizAnswer
        fields = ('question', 'question_text', 'question_type', 'text_answer', 'is_correct', 'points_awarded', 'explanation')
        read_only_fields = fields
