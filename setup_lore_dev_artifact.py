"""
One-time setup: Seed the Lore DB with a "Lore Engineering" collection
and a "Lore Project Build Document" artifact.

Usage (from repo root, with venv active):
    python setup_lore_dev_artifact.py
"""
import os
import sys

# Django bootstrap (mirrors manage.py logic)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from decouple import config
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f'lore.settings.{config("SETTINGS", default="dev")}')

import django
django.setup()

from django.core.files.base import ContentFile
from apps.collections.models import Collection
from apps.artifacts.models import Artifact, DocumentArtifact
from apps.artifacts.services import create_initial_version
from apps.accounts.models import User


def main():
    print("Setting up Lore Core Development collection and artifact...")

    # Get admin principal
    user = User.objects.filter(is_superuser=True).first() or User.objects.first()
    if not user:
        print("Error: No User found in database. Please register/create a user first.")
        return
    principal = user.principal

    # 1. Create or get "Lore Engineering" Collection
    collection = Collection.objects.filter(name="Lore Engineering").first()
    if not collection:
        collection = Collection.objects.create(name="Lore Engineering", owner=principal)
        print(f"Collection created: '{collection.name}' (ID: {collection.id})")
    else:
        print(f"Collection already exists: '{collection.name}' (ID: {collection.id})")

    # 2. Read project_build_document.md content
    doc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lore-frontend", "docs", "dev", "project_build_document.md")
    if not os.path.exists(doc_path):
        doc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md")

    with open(doc_path, "r", encoding="utf-8") as f:
        content_text = f.read()

    content_bytes = content_text.encode("utf-8")

    # 3. Create or update "Lore Project Build Document" Artifact
    artifact = Artifact.objects.filter(title="Lore Project Build Document").first()
    if not artifact:
        artifact = Artifact.objects.create(
            title="Lore Project Build Document",
            type="document",
            collection=collection,
            owner=principal,
            created_by=principal,
            lifecycle_state="approved",
        )
        # DocumentArtifact stores content as a FileField, not a TextField
        DocumentArtifact.objects.create(
            artifact=artifact,
            file=ContentFile(content_bytes, name="project_build_document.md"),
        )
        create_initial_version(artifact, content_text, principal)
        print(f"Artifact created: '{artifact.title}' (ID: {artifact.id})")
    else:
        # Update existing
        if hasattr(artifact, "document"):
            doc = artifact.document
            doc.file.save("project_build_document.md", ContentFile(content_bytes), save=True)
        else:
            DocumentArtifact.objects.create(
                artifact=artifact,
                file=ContentFile(content_bytes, name="project_build_document.md"),
            )
        artifact.collection = collection
        artifact.save()
        print(f"Artifact updated: '{artifact.title}' (ID: {artifact.id})")

    print(f"Success! Collection: {collection.id}, Artifact: {artifact.id}")


if __name__ == "__main__":
    main()
