from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.projects.models import Project
from apps.subjects.models import Subject

from .models import (
    AttemptStatusChoices,
    Choice,
    GenerationTypeChoices,
    Question,
    QuestionBankItem,
    QuestionTypeChoices,
    Quiz,
    QuizAttempt,
    QuizStatusChoices,
    StudentAnswer,
)
from .services import create_quiz, update_quiz


class QuizSubjectSerializer(serializers.ModelSerializer):
    education_stage_name = serializers.CharField(
        source='education_stage.name',
        read_only=True,
    )

    class Meta:
        model = Subject
        fields = (
            'id',
            'name',
            'education_stage',
            'education_stage_name',
            'grade_level',
            'description',
            'is_active',
        )


class ChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = ('id', 'text', 'order')


class ChoiceResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = ('id', 'text', 'is_correct', 'order')


class QuestionSerializer(serializers.ModelSerializer):
    choices = ChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = (
            'id',
            'text',
            'question_type',
            'difficulty_level',
            'order',
            'points',
            'choices',
        )


class QuestionResultSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    text = serializers.CharField()
    question_type = serializers.CharField()
    difficulty_level = serializers.CharField()
    order = serializers.IntegerField()
    points = serializers.IntegerField()
    choices = ChoiceResultSerializer(many=True)
    correct_choice = ChoiceResultSerializer(allow_null=True)
    selected_choice = ChoiceResultSerializer(allow_null=True)
    text_answer = serializers.CharField(allow_blank=True)
    is_correct = serializers.BooleanField()
    explanation = serializers.CharField(allow_blank=True)
    points_awarded = serializers.DecimalField(max_digits=8, decimal_places=2)


class QuizAttemptMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuizAttempt
        fields = (
            'id',
            'status',
            'started_at',
            'submitted_at',
            'score',
            'max_score',
            'percentage',
        )


class QuizListSerializer(serializers.ModelSerializer):
    subject = QuizSubjectSerializer(read_only=True)

    class Meta:
        model = Quiz
        # tuple[str, ...] so QuizDetailSerializer.Meta can extend this tuple
        # (`+= (...)`) without mypy flagging a fixed-length-tuple override.
        fields: tuple[str, ...] = (
            'id',
            'title',
            'project',
            'subject',
            'topic',
            'difficulty_level',
            'quiz_type',
            'generation_type',
            'status',
            'questions_count',
            'time_limit_minutes',
            'created_at',
        )


class QuizDetailSerializer(QuizListSerializer):
    questions = QuestionSerializer(many=True, read_only=True)
    attempts_count = serializers.SerializerMethodField()
    last_attempt = serializers.SerializerMethodField()

    class Meta(QuizListSerializer.Meta):
        fields = QuizListSerializer.Meta.fields + (
            'description',
            'ai_request_id',
            'updated_at',
            'questions',
            'attempts_count',
            'last_attempt',
        )

    @extend_schema_field(serializers.IntegerField())
    def get_attempts_count(self, obj):
        attempts = getattr(obj, 'attempts', None)
        if attempts is not None and hasattr(attempts, 'all'):
            return attempts.count()
        return obj.attempts.count()

    @extend_schema_field(QuizAttemptMiniSerializer(allow_null=True))
    def get_last_attempt(self, obj):
        attempts = getattr(obj, 'attempts', None)
        if attempts is not None and hasattr(attempts, 'all'):
            last_attempt = attempts.all().first()
        else:
            last_attempt = obj.attempts.order_by('-started_at').first()
        return QuizAttemptMiniSerializer(last_attempt).data if last_attempt else None


class QuizCreateSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.filter(is_deleted=False, status=Project.Status.ACTIVE),
        required=False,
        allow_null=True,
    )
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True, education_stage__is_active=True)
    )
    question_types = serializers.ListField(
        child=serializers.ChoiceField(choices=QuestionTypeChoices.choices),
        required=False,
        allow_empty=False,
    )

    class Meta:
        model = Quiz
        fields = (
            'project',
            'subject',
            'title',
            'description',
            'topic',
            'difficulty_level',
            'quiz_type',
            'generation_type',
            'questions_count',
            'time_limit_minutes',
            'question_types',
        )
        read_only_fields = ('questions_count',)

    def validate_generation_type(self, value):
        if value not in {GenerationTypeChoices.MANUAL, GenerationTypeChoices.AI}:
            raise serializers.ValidationError('Invalid generation type.')
        return value

    def validate(self, attrs):
        project = attrs.get('project')
        user = self.context['request'].user
        if not project:
            # Blueprint 01_BACKEND.md §3.1: no quiz -- manual or AI-generated
            # -- may be created outside a project.
            raise serializers.ValidationError({'project': 'A project is required to create a quiz.'})
        if project.owner_id != user.id:
            raise serializers.ValidationError({'project': 'Project not found or not owned by the current user.'})
        if project.subject_id and attrs['subject'].id != project.subject_id:
            raise serializers.ValidationError({'subject': 'Subject must match the selected project.'})
        return attrs

    def create(self, validated_data):
        user = self.context['request'].user
        return create_quiz(user, validated_data)


class QuizUpdateSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(choices=QuizStatusChoices.choices, required=False)

    class Meta:
        model = Quiz
        fields = (
            'title',
            'description',
            'topic',
            'difficulty_level',
            'quiz_type',
            'status',
            'time_limit_minutes',
        )

    def validate(self, attrs):
        requested_status = attrs.get('status')
        if requested_status == QuizStatusChoices.PUBLISHED:
            raise serializers.ValidationError({'status': 'Use the publish action so questions are validated first.'})
        if self.instance and self.instance.status == QuizStatusChoices.ARCHIVED:
            raise serializers.ValidationError('Archived quizzes cannot be edited.')
        if self.instance and self.instance.status == QuizStatusChoices.PUBLISHED:
            editable = set(attrs) - {'status'}
            if editable:
                raise serializers.ValidationError('Published quiz content is immutable. Archive it or create a new draft.')
            if requested_status not in {None, QuizStatusChoices.ARCHIVED}:
                raise serializers.ValidationError({'status': 'A published quiz may only be archived.'})
        return attrs

    def update(self, instance, validated_data):
        return update_quiz(instance, validated_data)


class StudentAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentAnswer
        fields = (
            'id',
            'question',
            'selected_choice',
            'text_answer',
            'created_at',
            'updated_at',
        )


class QuizAttemptSerializer(serializers.ModelSerializer):
    quiz = QuizListSerializer(read_only=True)

    class Meta:
        model = QuizAttempt
        fields = (
            'id',
            'quiz',
            'status',
            'started_at',
            'submitted_at',
            'score',
            'max_score',
            'percentage',
            'correct_answers_count',
            'wrong_answers_count',
            'unanswered_count',
            'duration_seconds',
        )


class QuizAttemptDetailQuizSerializer(serializers.ModelSerializer):
    subject = QuizSubjectSerializer(read_only=True)
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Quiz
        fields = (
            'id',
            'title',
            'description',
            'topic',
            'difficulty_level',
            'quiz_type',
            'generation_type',
            'status',
            'questions_count',
            'time_limit_minutes',
            'subject',
            'questions',
        )


class QuizAttemptDetailSerializer(serializers.ModelSerializer):
    quiz = QuizAttemptDetailQuizSerializer(read_only=True)
    answers = StudentAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = QuizAttempt
        fields = (
            'id',
            'quiz',
            'status',
            'started_at',
            'submitted_at',
            'score',
            'max_score',
            'percentage',
            'correct_answers_count',
            'wrong_answers_count',
            'unanswered_count',
            'duration_seconds',
            'answers',
        )


class StartAttemptSerializer(serializers.Serializer):
    """The start action intentionally accepts an empty JSON object."""

    def validate(self, attrs):
        return attrs


class QuizAttemptStartResponseSerializer(serializers.Serializer):
    attempt_id = serializers.IntegerField()
    quiz = QuizAttemptDetailQuizSerializer()
    questions = QuestionSerializer(many=True)
    started_at = serializers.DateTimeField()
    time_limit_minutes = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(choices=AttemptStatusChoices.choices)


class SubmitAnswerSerializer(serializers.Serializer):
    question = serializers.PrimaryKeyRelatedField(queryset=Question.objects.all())
    selected_choice = serializers.PrimaryKeyRelatedField(
        queryset=Choice.objects.all(),
        required=False,
        allow_null=True,
    )
    text_answer = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate(self, attrs):
        if not attrs.get('selected_choice') and not attrs.get('text_answer'):
            raise serializers.ValidationError(
                'Either selected_choice or text_answer must be provided.'
            )
        return attrs


class SubmitQuizSerializer(serializers.Serializer):
    answers = SubmitAnswerSerializer(many=True, required=False, default=list)


class QuizResultSerializer(serializers.Serializer):
    attempt = QuizAttemptSerializer()
    quiz = QuizDetailSerializer()
    answers = QuestionResultSerializer(many=True)
    correct_answers_count = serializers.IntegerField()
    wrong_answers_count = serializers.IntegerField()
    unanswered_count = serializers.IntegerField()
    percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    recommendations = serializers.ListField(child=serializers.CharField())


class QuestionBankItemSerializer(serializers.ModelSerializer):
    subject = QuizSubjectSerializer(read_only=True)

    class Meta:
        model = QuestionBankItem
        fields = (
            'id',
            'subject',
            'text',
            'question_type',
            'difficulty_level',
            'explanation',
            'is_public',
            'created_at',
            'updated_at',
        )


class ChoiceManageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = ("id", "text", "is_correct", "order")
        read_only_fields = ("id",)


class QuizQuestionManageSerializer(serializers.ModelSerializer):
    choices = ChoiceManageSerializer(many=True, required=False)

    class Meta:
        model = Question
        fields = (
            "id", "quiz", "text", "question_type", "difficulty_level", "explanation", "order", "points", "choices",
        )
        read_only_fields = ("id",)

    def validate_quiz(self, quiz):
        request = self.context["request"]
        if quiz.user_id != request.user.id:
            raise serializers.ValidationError("You do not own this quiz.")
        if quiz.generation_type != GenerationTypeChoices.MANUAL or quiz.status != QuizStatusChoices.DRAFT:
            raise serializers.ValidationError("Only manual draft quizzes can be edited.")
        return quiz

    def validate(self, attrs):
        question_type = attrs.get("question_type", getattr(self.instance, "question_type", QuestionTypeChoices.MCQ))
        if question_type == QuestionTypeChoices.SHORT_ANSWER:
            raise serializers.ValidationError({'question_type': 'Short-answer grading is not enabled yet.'})
        choices = attrs.get("choices")
        if choices is not None and question_type in {QuestionTypeChoices.MCQ, QuestionTypeChoices.TRUE_FALSE}:
            if len(choices) < 2:
                raise serializers.ValidationError({"choices": "At least two choices are required."})
            normalized = [item["text"].strip().casefold() for item in choices]
            if len(normalized) != len(set(normalized)):
                raise serializers.ValidationError({"choices": "Choices must be unique."})
            if sum(1 for item in choices if item.get("is_correct")) != 1:
                raise serializers.ValidationError({"choices": "Exactly one choice must be correct."})
        return attrs

    @staticmethod
    def _replace_choices(question, choices):
        question.choices.all().delete()
        Choice.objects.bulk_create([
            Choice(question=question, text=item["text"], is_correct=item.get("is_correct", False), order=item.get("order", index))
            for index, item in enumerate(choices, start=1)
        ])

    def create(self, validated_data):
        from django.db import transaction
        choices = validated_data.pop("choices", [])
        with transaction.atomic():
            question = Question.objects.create(**validated_data)
            self._replace_choices(question, choices)
            from .services import _sync_quiz_questions_count
            _sync_quiz_questions_count(question.quiz)
        return question

    def update(self, instance, validated_data):
        from django.db import transaction
        choices = validated_data.pop("choices", None)
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()
            if choices is not None:
                self._replace_choices(instance, choices)
        return instance
