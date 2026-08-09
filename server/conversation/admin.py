from django.contrib import admin

from .models import Extraction, Session, Suggestion, Turn


class TurnInline(admin.TabularInline):
    model = Turn
    extra = 0
    fields = ("speaker", "text", "started_at", "ended_at", "confidence")
    readonly_fields = fields


class ExtractionInline(admin.TabularInline):
    model = Extraction
    extra = 0
    fields = ("type", "text", "confidence", "turn")
    readonly_fields = fields


class SuggestionInline(admin.TabularInline):
    model = Suggestion
    extra = 0
    fields = ("question_text", "answer_text", "model_used", "latency_ms", "turn")
    readonly_fields = fields


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "mode", "started_at", "ended_at")
    inlines = [TurnInline, ExtractionInline, SuggestionInline]


@admin.register(Turn)
class TurnAdmin(admin.ModelAdmin):
    list_display = ("session", "speaker", "text", "started_at", "confidence")
    list_filter = ("speaker",)


@admin.register(Extraction)
class ExtractionAdmin(admin.ModelAdmin):
    list_display = ("session", "type", "text", "confidence", "created_at")
    list_filter = ("type",)


@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = ("session", "question_text", "answer_text", "model_used", "latency_ms", "created_at")
    list_filter = ("model_used",)
