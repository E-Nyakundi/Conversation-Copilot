# Phase 5 (2026-08-16): code-vs-prose answer generation. Adds
# answer_format (prose/code/code_and_prose — see ai/answer_format.py for
# the classifier that decides which) and secondary_answer_text, which
# holds the companion prose answer when answer_format is
# code_and_prose (answer_text holds the code answer in that case — see
# Suggestion's docstring in conversation/models.py for why one row with
# two text fields, not two separate rows).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversation', '0005_extraction_subtype'),
    ]

    operations = [
        migrations.AddField(
            model_name='suggestion',
            name='answer_format',
            field=models.CharField(
                choices=[
                    ('prose', 'Prose'),
                    ('code', 'Code'),
                    ('code_and_prose', 'Code and prose'),
                ],
                default='prose',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='suggestion',
            name='secondary_answer_text',
            field=models.TextField(blank=True, null=True),
        ),
    ]
