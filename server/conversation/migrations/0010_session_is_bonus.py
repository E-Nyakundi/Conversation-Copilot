# Session-cap fix (2026-09-01): flags a Session as a bonus-grace session
# (15-minute cap, never counted against quota/usage) rather than a normal
# billable session (120-minute cap).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversation', '0009_session_owner_userprofile_owner_alter_userprofile_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='is_bonus',
            field=models.BooleanField(default=False),
        ),
    ]
