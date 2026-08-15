# Phase 2 context-window follow-up (2026-08-14): tags which Extraction
# rows came from the imperative-ask pattern in intent.py, so
# consumers.py._generate_suggestion can widen the context window for
# those specifically. See ExtractedItem.subtype / QUESTION_PATTERNS in
# conversation/intent.py and Extraction.subtype in conversation/models.py
# for the full rationale.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversation', '0004_suggestion_answer_level'),
    ]

    operations = [
        migrations.AddField(
            model_name='extraction',
            name='subtype',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
