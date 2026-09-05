from django.contrib import admin

from .models import (
    Choice,
    Question,
    QuestionBankItem,
    Quiz,
    QuizAttempt,
    QuizProgressLog,
    StudentAnswer,
)


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = (
        'order',
        'text',
        'question_type',
        'difficulty_level',
        'points',
    )
    show_change_link = True


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0
    fields = ('order', 'text', 'is_correct')


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'user',
        'subject',
        'difficulty_level',
        'quiz_type',
        'generation_type',
        'status',
        'questions_count',
        'created_at',
    )
    list_filter = (
        'subject',
        'difficulty_level',
        'quiz_type',
        'generation_type',
        'status',
    )
    search_fields = ('title', 'topic', 'user__email')
    autocomplete_fields = ('user', 'subject')
    readonly_fields = ('questions_count', 'ai_request_id', 'created_at', 'updated_at')
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'quiz',
        'question_type',
        'difficulty_level',
        'order',
        'points',
    )
    list_filter = ('question_type', 'difficulty_level')
    search_fields = ('text',)
    autocomplete_fields = ('quiz',)
    inlines = [ChoiceInline]
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ('id', 'question', 'order', 'is_correct')
    list_filter = ('is_correct',)
    search_fields = ('text', 'question__text')
    autocomplete_fields = ('question',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'user',
        'quiz',
        'status',
        'percentage',
        'started_at',
        'submitted_at',
    )
    list_filter = ('status', 'quiz__subject')
    search_fields = ('user__email', 'quiz__title')
    autocomplete_fields = ('user', 'quiz')
    readonly_fields = (
        'score',
        'max_score',
        'percentage',
        'correct_answers_count',
        'wrong_answers_count',
        'unanswered_count',
        'duration_seconds',
        'started_at',
        'submitted_at',
        'created_at',
        'updated_at',
    )


@admin.register(StudentAnswer)
class StudentAnswerAdmin(admin.ModelAdmin):
    list_display = ('id', 'attempt', 'question', 'selected_choice', 'is_correct', 'points_awarded')
    list_filter = ('is_correct', 'question__question_type')
    search_fields = ('question__text', 'attempt__quiz__title', 'attempt__user__email')
    autocomplete_fields = ('attempt', 'question', 'selected_choice')
    readonly_fields = ('is_correct', 'points_awarded', 'created_at', 'updated_at')


@admin.register(QuestionBankItem)
class QuestionBankItemAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'subject',
        'created_by',
        'question_type',
        'difficulty_level',
        'is_public',
        'is_active',
        'created_at',
    )
    list_filter = ('subject', 'question_type', 'difficulty_level', 'is_public', 'is_active')
    search_fields = ('text', 'subject__name', 'created_by__email')
    autocomplete_fields = ('subject', 'created_by')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(QuizProgressLog)
class QuizProgressLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'user', 'quiz', 'attempt', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('user__email', 'quiz__title')
    autocomplete_fields = ('user', 'quiz', 'attempt')
    readonly_fields = ('user', 'quiz', 'attempt', 'action', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
