from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversation", "0006_suggestion_answer_format"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                (
                    "id",
                    models.IntegerField(default=1, editable=False, primary_key=True, serialize=False),
                ),
                ("name", models.CharField(blank=True, max_length=150)),
                (
                    "role",
                    models.CharField(
                        blank=True,
                        help_text="Job title / role / field, e.g. 'Backend engineer' or 'Sales rep'.",
                        max_length=150,
                    ),
                ),
                (
                    "bio",
                    models.TextField(
                        blank=True,
                        help_text=(
                            "Free-text background, personality, career details — "
                            "whatever should inform answers given as this person."
                        ),
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
