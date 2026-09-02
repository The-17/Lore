import re
from typing import TYPE_CHECKING
from django.db import transaction
from apps.common.realtime import publish_artifact_event

if TYPE_CHECKING:
    from apps.artifacts.models import Artifact
    from apps.accounts.models import Principal


WIKI_LINK_REGEX = re.compile(r"\[\[(.*?)\]\]")


def parse_wiki_link_titles(text_content: str) -> list[str]:
    """
    Extracts unique artifact titles from [[Wiki-Link]] syntax,
    supporting standard pipe aliases like [[Target Title|Alias Text]].
    """
    if not text_content:
        return []

    raw_matches = WIKI_LINK_REGEX.findall(text_content)
    titles = set()
    for match in raw_matches:
        cleaned = match.split("|")[0].strip()
        if cleaned:
            titles.add(cleaned)
    return list(titles)


def extract_and_sync_wiki_links(
    artifact: "Artifact",
    text_content: str,
    created_by: "Principal",
) -> list[str]:
    """
    Scans text_content for [[Artifact Title]] syntax.
    Finds existing artifacts matching those titles within the workspace,
    synchronizes 'references' relationships, and prunes stale edges.
    Broadcasts real-time graph events upon changes.
    """
    from apps.artifacts.models import Artifact, ArtifactRelationship

    target_titles = parse_wiki_link_titles(text_content)

    with transaction.atomic():
        # Find target artifacts by title within the same owner workspace (excluding self)
        target_artifacts = Artifact.objects.filter(
            owner=artifact.owner,
            title__in=target_titles,
            deleted_at__isnull=True,
        ).exclude(id=artifact.id) if target_titles else Artifact.objects.none()

        target_ids = set(target_artifacts.values_list("id", flat=True))

        # 1. Prune stale 'references' edges that are no longer referenced in text
        stale_edges = ArtifactRelationship.objects.filter(
            from_artifact=artifact,
            relation_type="references",
        ).exclude(to_artifact_id__in=target_ids)

        pruned_count, _ = stale_edges.delete()
        if pruned_count > 0:
            publish_artifact_event(
                event_type="artifact.relationships_pruned",
                artifact_id=str(artifact.id),
                payload={"pruned_count": pruned_count},
                owner_id=str(artifact.owner_id),
            )

        # 2. Upsert active 'references' edges
        resolved_titles = []
        for target in target_artifacts:
            rel, created = ArtifactRelationship.objects.get_or_create(
                from_artifact=artifact,
                to_artifact=target,
                relation_type="references",
                defaults={"created_by": created_by},
            )
            resolved_titles.append(target.title)
            if created:
                publish_artifact_event(
                    event_type="artifact.relationship_created",
                    artifact_id=str(artifact.id),
                    payload={
                        "to_artifact_id": str(target.id),
                        "to_artifact_title": target.title,
                        "relation_type": "references",
                    },
                    owner_id=str(artifact.owner_id),
                )

    return resolved_titles
