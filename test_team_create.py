import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
django.setup()

from apps.teams.models import Team
from apps.accounts.models import User
import uuid

user = User.objects.first()
if not user:
    user = User.objects.create(username="testuser", email="test@test.com")

try:
    name = str(uuid.uuid4())[:8]
    team = Team.objects.create(name=name, created_by=user)
    print(f"Team created successfully: {team.id} - {team.name}")
except Exception as e:
    import traceback
    traceback.print_exc()
