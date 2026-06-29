from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import zipfile

import yaml

from backend.app.artifacts import _render_text_pdf
from backend.app.config import Settings


@dataclass(frozen=True)
class MopBundleFile:
    relative_path: str
    absolute_path: Path
    filename: str
    artifact_type: str
    mime_type: str
    title: str
    publish: bool = True


@dataclass(frozen=True)
class MopBundleResult:
    bundle_root: Path
    bundle_id: str
    timestamp: str
    source_namespace: str
    target_namespace: str
    generated_release_name: str
    human_mop_markdown: str
    installation_markdown: str
    machine_execution_plan: str
    artifact_metadata: dict
    artifact_index: dict
    files: list[MopBundleFile]
    publish_files: list[MopBundleFile]
    warnings: list[str]
    validation: dict


class MopBundleBuilder:
    """Builds the deterministic MoP artifact bundle required by ESDA V1."""

    def __init__(self, *, settings: Settings, storage_root: str | Path) -> None:
        self.settings = settings
        self.storage_root = Path(storage_root)

    def build(
        self,
        *,
        run_id: str,
        user_id: str,
        source_namespace: str,
        target_namespace: str,
        target_environment: str,
        change_intent: str,
        helm_release: str | None,
        implementation_window: str | None,
        analysis_depth: str,
        model_profile: dict,
        plan: dict,
        classification: dict,
        tool_results: dict,
        validation: dict,
        recovery: dict,
        draft_markdown: str,
        source_evidence_summary: str,
        limitations: list[str],
        agent_artifact_payloads: dict[str, bytes] | None = None,
    ) -> MopBundleResult:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        bundle_id = _safe_token(run_id.removeprefix("mop_"))[:36]
        source = _safe_name(source_namespace)
        target = _safe_name(target_namespace)
        generated_release_name = f"{self.settings.mop_generated_name_prefix}-{source}"
        bundle_root = self.storage_root / "mop_bundle_workspace" / _safe_token(run_id) / "bundle"
        if bundle_root.exists():
            shutil.rmtree(bundle_root)
        deployment_root = bundle_root / "deployment-artifacts"
        helm_chart_dir = deployment_root / "helm-chart"
        extracted_dir = helm_chart_dir / "extracted"
        values_dir = deployment_root / "helm-values"
        k8s_dir = deployment_root / "kubernetes-manifests"
        crd_dir = k8s_dir / "crds"
        rendered_dir = deployment_root / "rendered-manifests"
        for directory in [helm_chart_dir, extracted_dir, values_dir, k8s_dir, crd_dir, rendered_dir]:
            directory.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        agent_artifact_payloads = agent_artifact_payloads or {}
        doc_refs = self._reference_documents()
        chart = self._chart_identity(
            source_namespace=source,
            helm_release=helm_release,
            helm_result=tool_results.get("helm_manager") or {},
            warnings=warnings,
        )
        values_rel = f"helm-values/values-{generated_release_name}.yaml"
        values_path = deployment_root / values_rel
        self._write_text(values_path, self._target_values_yaml(chart_name=chart["name"], source=source))

        namespace_rel = f"kubernetes-manifests/namespace-{target}.yaml"
        namespace_path = deployment_root / namespace_rel
        self._write_text(namespace_path, self._namespace_manifest(source=source, target=target))

        ingress_rel: str | None = None
        if self._source_has_ingress(source, tool_results.get("k8s_inspector") or {}):
            ingress_rel = f"kubernetes-manifests/ingress-{generated_release_name}.yaml"
            self._write_text(
                deployment_root / ingress_rel,
                self._ingress_manifest(source=source, target=target, release=generated_release_name),
            )
        raw_configmap_rels = self._write_raw_configmaps(
            deployment_root=deployment_root,
            payloads=agent_artifact_payloads,
            warnings=warnings,
        )

        chart_files = self._materialize_chart(
            chart=chart,
            helm_chart_dir=helm_chart_dir,
            extracted_dir=extracted_dir,
            values_path=values_path,
            rendered_dir=rendered_dir,
            generated_release_name=generated_release_name,
            target_namespace=target,
            warnings=warnings,
        )
        crd_rels = self._copy_crds(chart_files.get("extracted_chart_path"), crd_dir)
        rendered_rel = chart_files.get("rendered_manifest_rel")

        helm_commands = self._helm_commands(
            chart=chart,
            generated_release_name=generated_release_name,
            target_namespace=target,
            values_rel=values_rel,
            rendered_rel=rendered_rel,
            ingress_rel=ingress_rel,
        )
        self._write_text(deployment_root / "helm-commands.md", helm_commands)

        artifact_index = self._artifact_index(
            source=source,
            target=target,
            generated_release_name=generated_release_name,
            chart=chart,
            chart_files=chart_files,
            values_rel=values_rel,
            namespace_rel=namespace_rel,
            ingress_rel=ingress_rel,
            raw_configmap_rels=raw_configmap_rels,
            crd_rels=crd_rels,
            rendered_rel=rendered_rel,
            warnings=warnings,
        )
        self._write_json(deployment_root / "artifact-index.json", artifact_index)

        human_name = f"mop-{source}-to-{target}-{timestamp}.human-mop.md"
        install_name = f"mop-{source}-to-{target}-{timestamp}.installation.md"
        pdf_name = f"mop-{source}-to-{target}-{timestamp}.pdf"
        plan_name = "machine_execution_plan.yaml"
        metadata_name = "artifact.json"
        deployment_zip_name = "deployment-artifacts.zip"
        bundle_zip_name = "mop-bundle.zip"

        machine_plan = self._machine_execution_plan(
            source=source,
            target=target,
            generated_release_name=generated_release_name,
            chart=chart,
            values_rel=values_rel,
            namespace_rel=namespace_rel,
            ingress_rel=ingress_rel,
            rendered_rel=rendered_rel,
        )
        human_mop = self._human_mop_markdown(
            source=source,
            target=target,
            run_id=run_id,
            bundle_id=bundle_id,
            timestamp=timestamp,
            change_intent=change_intent,
            target_environment=target_environment,
            helm_release=helm_release,
            implementation_window=implementation_window,
            analysis_depth=analysis_depth,
            generated_release_name=generated_release_name,
            chart=chart,
            artifact_index=artifact_index,
            source_evidence_summary=source_evidence_summary,
            limitations=limitations,
            validation=validation,
            recovery=recovery,
            draft_markdown=draft_markdown,
            warnings=warnings,
        )
        installation = self._installation_markdown(
            source=source,
            target=target,
            run_id=run_id,
            timestamp=timestamp,
            change_intent=change_intent,
            target_environment=target_environment,
            generated_release_name=generated_release_name,
            chart=chart,
            artifact_index=artifact_index,
            warnings=warnings,
        )
        artifact_metadata = self._artifact_metadata(
            run_id=run_id,
            user_id=user_id,
            source=source,
            target=target,
            timestamp=timestamp,
            generated_release_name=generated_release_name,
            change_intent=change_intent,
            target_environment=target_environment,
            helm_release=helm_release,
            implementation_window=implementation_window,
            analysis_depth=analysis_depth,
            model_profile=model_profile,
            classification=classification,
            plan=plan,
            tool_results=tool_results,
            validation=validation,
            recovery=recovery,
            doc_refs=doc_refs,
            artifact_index=artifact_index,
            warnings=warnings,
        )

        agent_human = self._first_agent_payload(agent_artifact_payloads, (".human-mop.md",))
        agent_install = self._first_agent_payload(agent_artifact_payloads, (".installation.md",))
        agent_plan = self._first_agent_payload(agent_artifact_payloads, ("machine_execution_plan.yaml",))
        agent_pdf = self._first_agent_payload(agent_artifact_payloads, (".pdf",))
        agent_metadata = self._first_agent_payload(agent_artifact_payloads, ("artifact.json",))
        primary_agent_paths = {
            rel_path for rel_path, _payload in [agent_human, agent_install, agent_plan, agent_pdf, agent_metadata] if rel_path
        }
        if agent_human[1]:
            human_name = Path(agent_human[0]).name
            human_mop = self._decode_agent_text(agent_human[1])
            warnings.append("using_mop_creation_agent_professional_markdown_template")
        if agent_install[1]:
            install_name = Path(agent_install[0]).name
            installation = self._decode_agent_text(agent_install[1])
        if agent_plan[1]:
            machine_plan = self._decode_agent_text(agent_plan[1])
        if agent_pdf[1]:
            pdf_name = Path(agent_pdf[0]).name
            warnings.append("using_mop_creation_agent_professional_pdf_template")
        else:
            warnings.append("mop_creation_agent_professional_pdf_unavailable: generated fallback text PDF")

        artifact_metadata["mop_creation_agent_artifacts"] = {
            "available": bool(agent_artifact_payloads),
            "preserved_paths": sorted(agent_artifact_payloads),
            "professional_markdown_used": bool(agent_human[1]),
            "professional_pdf_used": bool(agent_pdf[1]),
            "human_mop_pdf_renderer": {
                "generated_from": "professional_mop_pdf_template",
                "renderer": "phase7_professional_pdf_renderer",
                "template_id": "bosgenesis_professional_mop_pdf",
                "template_version": "1.1",
                "source": "bosgenesis-mop-creation-agent",
            } if agent_pdf[1] else None,
        }

        self._write_text(bundle_root / human_name, human_mop)
        self._write_text(bundle_root / install_name, installation)
        self._write_text(bundle_root / plan_name, machine_plan)
        if agent_metadata[1]:
            self._write_bytes(bundle_root / metadata_name, agent_metadata[1])
            self._write_json(bundle_root / "esda-artifact.json", artifact_metadata)
        else:
            self._write_json(bundle_root / metadata_name, artifact_metadata)
        if agent_pdf[1]:
            self._write_bytes(bundle_root / pdf_name, agent_pdf[1])
        else:
            self._write_bytes(bundle_root / pdf_name, _render_text_pdf(markdown=human_mop, title=f"MoP - {source} to {target}"))
        self._write_agent_payloads(bundle_root, agent_artifact_payloads, primary_agent_paths)
        self._zip_deployment_artifacts(deployment_root, bundle_root / deployment_zip_name)
        self._zip_complete_bundle(bundle_root, bundle_root / bundle_zip_name)

        bundle_validation = self._validate_bundle(
            bundle_root=bundle_root,
            deployment_root=deployment_root,
            chart=chart,
            chart_files=chart_files,
            rendered_rel=rendered_rel,
            ingress_rel=ingress_rel,
            warnings=warnings,
        )
        artifact_metadata["bundle_validation"] = bundle_validation
        if agent_metadata[1]:
            self._write_json(bundle_root / "esda-artifact.json", artifact_metadata)
        else:
            self._write_json(bundle_root / metadata_name, artifact_metadata)
        self._zip_complete_bundle(bundle_root, bundle_root / bundle_zip_name)

        files = [
            MopBundleFile(metadata_name, bundle_root / metadata_name, metadata_name, "mop_metadata", "application/json", "MoP artifact metadata"),
            MopBundleFile(plan_name, bundle_root / plan_name, plan_name, "mop_plan", "application/x-yaml", "MoP machine execution plan"),
            MopBundleFile(human_name, bundle_root / human_name, human_name, "mop", "text/markdown; charset=utf-8", f"MoP - {source} to {target}"),
            MopBundleFile(install_name, bundle_root / install_name, install_name, "mop_installation", "text/markdown; charset=utf-8", f"MoP installation notes - {source} to {target}"),
            MopBundleFile(pdf_name, bundle_root / pdf_name, pdf_name, "mop_pdf", "application/pdf", f"MoP PDF - {source} to {target}"),
            MopBundleFile(deployment_zip_name, bundle_root / deployment_zip_name, deployment_zip_name, "mop_deployment_zip", "application/zip", f"MoP deployment artifacts - {source} to {target}"),
            MopBundleFile(bundle_zip_name, bundle_root / bundle_zip_name, bundle_zip_name, "mop_bundle_zip", "application/zip", f"Complete MoP bundle - {source} to {target}"),
        ]
        return MopBundleResult(
            bundle_root=bundle_root,
            bundle_id=bundle_id,
            timestamp=timestamp,
            source_namespace=source,
            target_namespace=target,
            generated_release_name=generated_release_name,
            human_mop_markdown=human_mop,
            installation_markdown=installation,
            machine_execution_plan=machine_plan,
            artifact_metadata=artifact_metadata,
            artifact_index=artifact_index,
            files=files,
            publish_files=[file for file in files if file.publish],
            warnings=warnings,
            validation=bundle_validation,
        )


    @staticmethod
    def _first_agent_payload(payloads: dict[str, bytes], suffixes: tuple[str, ...]) -> tuple[str, bytes | None]:
        lowered_suffixes = tuple(item.lower() for item in suffixes)
        for rel_path, payload in payloads.items():
            normalized = rel_path.replace("\\", "/").lower()
            if any(normalized.endswith(suffix) for suffix in lowered_suffixes):
                return rel_path, payload
        return "", None

    @staticmethod
    def _decode_agent_text(payload: bytes) -> str:
        return payload.decode("utf-8", errors="replace")

    def _write_agent_payloads(self, bundle_root: Path, payloads: dict[str, bytes], primary_paths: set[str]) -> None:
        for rel_path, payload in payloads.items():
            safe_path = self._safe_agent_relative_path(rel_path)
            if not safe_path or rel_path in primary_paths:
                continue
            destination = bundle_root / safe_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)

    def _write_raw_configmaps(
        self,
        *,
        deployment_root: Path,
        payloads: dict[str, bytes],
        warnings: list[str],
    ) -> list[str]:
        raw_root = deployment_root / "kubernetes-manifests" / "raw"
        copied: list[str] = []
        for rel_path, payload in payloads.items():
            safe_path = self._safe_agent_relative_path(rel_path)
            if not safe_path or not self._is_raw_configmap_payload(safe_path=safe_path, payload=payload):
                continue
            destination = raw_root / safe_path.name
            if destination.exists():
                suffix = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:8]
                destination = raw_root / f"{destination.stem}-{suffix}{destination.suffix}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            copied.append(f"kubernetes-manifests/raw/{destination.name}")
        if copied:
            warnings.append(f"raw_configmaps_included:{len(copied)}")
        return copied

    @staticmethod
    def _is_raw_configmap_payload(*, safe_path: Path, payload: bytes) -> bool:
        name = safe_path.name.lower()
        if not name.endswith((".yaml", ".yml")):
            return False
        if name.startswith("configmap-"):
            return True
        text = payload.decode("utf-8", errors="ignore")
        return bool(re.search(r"(?im)^\s*kind:\s*ConfigMap\s*$", text))

    @staticmethod
    def _safe_agent_relative_path(rel_path: str) -> Path | None:
        normalized = rel_path.replace("\\", "/").strip("/")
        if not normalized or ".." in normalized.split("/"):
            return None
        return Path(normalized)

    def _reference_documents(self) -> list[dict]:
        refs = []
        for path in [
            Path("knowledge-base/mop-generation/plan.md"),
            Path("knowledge-base/mop-generation/ESDA_MOP_ARTIFACT_BUNDLE_GENERATION.md"),
        ]:
            if not path.exists():
                refs.append({"path": path.as_posix(), "available": False})
                continue
            data = path.read_bytes()
            refs.append(
                {
                    "path": path.as_posix(),
                    "available": True,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
        return refs

    def _chart_identity(
        self,
        *,
        source_namespace: str,
        helm_release: str | None,
        helm_result: dict,
        warnings: list[str],
    ) -> dict:
        if source_namespace == "signoz" or helm_release == "signoz":
            return {
                "name": "signoz",
                "version": "0.122.0",
                "source_release": helm_release or "signoz",
                "repo_name": "signoz",
                "repo_url": "https://charts.signoz.io",
                "package_url": "https://github.com/SigNoz/charts/releases/download/signoz-0.122.0/signoz-0.122.0.tgz",
                "helm_managed": True,
                "source": "known_public_chart",
            }
        release = self._first_helm_release(helm_result)
        if release:
            name = str(release.get("name") or helm_release or source_namespace)
            chart_ref = str(release.get("chart") or release.get("chart_name") or "")
            chart_name, chart_version = _split_chart_ref(chart_ref)
            repo_url = str(release.get("repo_url") or release.get("repository") or "").strip()
            if chart_name and chart_version and repo_url:
                return {
                    "name": chart_name,
                    "version": chart_version,
                    "source_release": name,
                    "repo_name": _safe_name(chart_name),
                    "repo_url": repo_url,
                    "package_url": None,
                    "helm_managed": True,
                    "source": "helm_manager_evidence",
                }
            warnings.append(
                "helm_chart_source_incomplete: Helm evidence did not include a public chart repo URL and version."
            )
        else:
            warnings.append("helm_release_evidence_missing: no Helm release metadata was available.")
        return {
            "name": source_namespace,
            "version": "unknown",
            "source_release": helm_release or source_namespace,
            "repo_name": source_namespace,
            "repo_url": None,
            "package_url": None,
            "helm_managed": False,
            "source": "evidence_only",
        }

    def _materialize_chart(
        self,
        *,
        chart: dict,
        helm_chart_dir: Path,
        extracted_dir: Path,
        values_path: Path,
        rendered_dir: Path,
        generated_release_name: str,
        target_namespace: str,
        warnings: list[str],
    ) -> dict:
        if not chart.get("helm_managed") or not chart.get("repo_url") or chart.get("version") == "unknown":
            warnings.append("helm_artifacts_not_generated: chart source is not known; generated bundle is evidence-only for Helm files.")
            return {}
        helm = shutil.which("helm")
        if not helm:
            warnings.append("helm_cli_missing: helm was not found in PATH; chart package and rendered manifest were not generated.")
            return {}
        chart_name = chart["name"]
        version = chart["version"]
        repo_url = chart["repo_url"]
        package_rel = f"helm-chart/{chart_name}-{version}.tgz"
        index_rel = f"helm-chart/{chart['repo_name']}-index.yaml"
        extracted_rel = f"helm-chart/extracted/{chart_name}"
        rendered_rel = f"rendered-manifests/{generated_release_name}-rendered.yaml"
        package_path = helm_chart_dir / f"{chart_name}-{version}.tgz"
        try:
            self._run_helm(
                helm,
                ["pull", chart_name, "--repo", repo_url, "--version", version, "--destination", str(helm_chart_dir)],
            )
            self._run_helm(
                helm,
                [
                    "pull",
                    chart_name,
                    "--repo",
                    repo_url,
                    "--version",
                    version,
                    "--untar",
                    "--untardir",
                    str(extracted_dir),
                ],
            )
        except Exception as exc:
            warnings.append(f"helm_pull_failed: {exc}")
            return {}
        self._download_index(repo_url, helm_chart_dir / f"{chart['repo_name']}-index.yaml", warnings)
        extracted_chart_path = extracted_dir / chart_name
        rendered_path = rendered_dir / f"{generated_release_name}-rendered.yaml"
        try:
            result = self._run_helm(
                helm,
                [
                    "template",
                    generated_release_name,
                    str(extracted_chart_path),
                    "--namespace",
                    target_namespace,
                    "--values",
                    str(values_path),
                ],
            )
            self._write_text(rendered_path, result.stdout)
            if not rendered_path.read_text(encoding="utf-8").strip():
                warnings.append("helm_render_empty: helm template produced an empty manifest.")
        except Exception as exc:
            warnings.append(f"helm_render_failed: {exc}")
            if rendered_path.exists():
                rendered_path.unlink()
            rendered_rel = None
        return {
            "package_rel": package_rel if package_path.exists() else None,
            "index_rel": index_rel if (helm_chart_dir / f"{chart['repo_name']}-index.yaml").exists() else None,
            "extracted_chart_rel": extracted_rel if extracted_chart_path.exists() else None,
            "extracted_chart_path": extracted_chart_path if extracted_chart_path.exists() else None,
            "rendered_manifest_rel": rendered_rel,
        }

    @staticmethod
    def _run_helm(helm: str, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [helm, *args],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "helm command failed").strip()
            raise RuntimeError(message[:1600])
        return result

    @staticmethod
    def _download_index(repo_url: str, destination: Path, warnings: list[str]) -> None:
        try:
            with urllib.request.urlopen(repo_url.rstrip("/") + "/index.yaml", timeout=30) as response:
                destination.write_bytes(response.read())
        except Exception as exc:
            warnings.append(f"helm_index_download_failed: {exc}")

    @staticmethod
    def _copy_crds(extracted_chart_path: Path | None, crd_dir: Path) -> list[str]:
        if not extracted_chart_path or not extracted_chart_path.exists():
            return []
        copied: list[str] = []
        for path in extracted_chart_path.rglob("crds/*.yaml"):
            destination = crd_dir / path.name
            shutil.copy2(path, destination)
            copied.append(f"kubernetes-manifests/crds/{destination.name}")
        return copied

    @staticmethod
    def _target_values_yaml(*, chart_name: str, source: str) -> str:
        top_key = chart_name or source
        return f"""global:
  storageClass: local-path

{top_key}:
  ingress:
    enabled: false
"""

    @staticmethod
    def _namespace_manifest(*, source: str, target: str) -> str:
        return f"""apiVersion: v1
kind: Namespace
metadata:
  name: {target}
  labels:
    bosgenesis.io/generated-by: codex-mop-artifact-rerun
    bosgenesis.io/source-namespace: {source}
"""

    @staticmethod
    def _ingress_manifest(*, source: str, target: str, release: str) -> str:
        return f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {release}
  namespace: {target}
  labels:
    app.kubernetes.io/name: {source}
    app.kubernetes.io/instance: {release}
    bosgenesis.io/generated-by: codex-mop-artifact-rerun
    bosgenesis.io/source-namespace: {source}
  annotations:
    bosgenesis.io/source-ingress: {source}
    bosgenesis.io/runtime-prefix: agent-ai
spec:
  ingressClassName: nginx
  rules:
    - host: {source}-{target}.bosgenesis.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {release}
                port:
                  number: 8080
"""

    @staticmethod
    def _helm_commands(
        *,
        chart: dict,
        generated_release_name: str,
        target_namespace: str,
        values_rel: str,
        rendered_rel: str | None,
        ingress_rel: str | None,
    ) -> str:
        chart_path = f"./helm-chart/extracted/{chart['name']}"
        rendered_target = f"./{rendered_rel or f'rendered-manifests/{generated_release_name}-rendered.yaml'}"
        version = chart.get("version") or "unknown"
        ingress_command = (
            f"kubectl apply -f ./{ingress_rel}"
            if ingress_rel
            else "# No source ingress was detected; no generated ingress manifest is included."
        )
        return f"""# BOS Genesis generated deployment artifact commands

No mutation was performed while generating this bundle.

## Helm dry-run render
helm template {generated_release_name} {chart_path} `
  --namespace {target_namespace} `
  --values ./{values_rel} `
  --version {version} > {rendered_target}

## Helm dry-run install
helm upgrade --install {generated_release_name} {chart_path} `
  --namespace {target_namespace} `
  --create-namespace `
  --values ./{values_rel} `
  --dry-run

## Governed mutation command, only after dry-run and approval
helm upgrade --install {generated_release_name} {chart_path} `
  --namespace {target_namespace} `
  --create-namespace `
  --values ./{values_rel} `
  --atomic `
  --timeout 10m

## Agent-generated ingress, apply only after Helm services exist
{ingress_command}
"""

    @staticmethod
    def _artifact_index(
        *,
        source: str,
        target: str,
        generated_release_name: str,
        chart: dict,
        chart_files: dict,
        values_rel: str,
        namespace_rel: str,
        ingress_rel: str | None,
        raw_configmap_rels: list[str],
        crd_rels: list[str],
        rendered_rel: str | None,
        warnings: list[str],
    ) -> dict:
        manifests = [namespace_rel]
        if ingress_rel:
            manifests.append(ingress_rel)
        manifests.extend(raw_configmap_rels)
        chart_payload = {
            "name": chart["name"],
            "version": chart["version"],
            "source_release": chart["source_release"],
            "package": chart_files.get("package_rel"),
            "extracted_chart": chart_files.get("extracted_chart_rel"),
            "repo_url": chart.get("repo_url"),
            "package_url": chart.get("package_url"),
            "helm_managed": chart.get("helm_managed"),
            "source": chart.get("source"),
        }
        return {
            "artifact_type": "bosgenesis_deployment_artifacts",
            "source_namespace": source,
            "target_namespace_placeholder": target,
            "generated_release_name": generated_release_name,
            "chart": chart_payload,
            "values": [values_rel],
            "kubernetes_manifests": manifests,
            "raw_configmaps": raw_configmap_rels,
            "crds": crd_rels,
            "rendered_manifests": [rendered_rel] if rendered_rel else [],
            "commands": "helm-commands.md",
            "warnings": warnings,
            "policy_notes": [
                "No mutation was performed during artifact generation.",
                "Ingress is generated separately with agent-ai prefix only when a source ingress exists.",
                "Secrets are not copied from source namespace.",
            ],
        }

    @staticmethod
    def _machine_execution_plan(
        *,
        source: str,
        target: str,
        generated_release_name: str,
        chart: dict,
        values_rel: str,
        namespace_rel: str,
        ingress_rel: str | None,
        rendered_rel: str | None,
    ) -> str:
        plan = {
            "machine_execution_plan": {
                "schema_version": "1.0",
                "authority_order": "observed_evidence > deterministic_normalization > llm_suggestion > human_fill_in",
                "executor_contract": {
                    "parse_this_block_first": True,
                    "dry_run_before_mutation": True,
                    "human_approval_before_mutation": True,
                    "never_copy_secret_values": True,
                    "target_namespace_placeholder_only": target,
                    "llm_suggestions_are_not_authority": True,
                },
                "dependency_graph": [
                    {"phase_id": "verify_access", "depends_on": []},
                    {"phase_id": "prepare_target_namespace", "depends_on": ["verify_access"]},
                    {"phase_id": "prepare_secret_placeholders", "depends_on": ["prepare_target_namespace"]},
                    {"phase_id": "install_helm_releases", "depends_on": ["prepare_secret_placeholders"]},
                    {"phase_id": "apply_ingress", "depends_on": ["install_helm_releases"]},
                    {"phase_id": "validate", "depends_on": ["apply_ingress"]},
                ],
                "required_human_inputs": [
                    "Approved secret values must be supplied from approved secure sources if the chart requires them."
                ],
                "phases": [
                    {
                        "phase_id": "verify_access",
                        "objective": "Confirm artifact bundle, source evidence, and deferred namespace binding intent.",
                        "steps": [
                            {
                                "step_id": "verify-artifact-bundle",
                                "title": "Verify generated artifact bundle",
                                "type": "context_check",
                                "commands": ["test -f artifact.json && test -d deployment-artifacts"],
                                "mutates_target": False,
                                "requires_human_approval": False,
                                "evidence_refs": ["artifact.json", "deployment-artifacts/artifact-index.json"],
                            }
                        ],
                    },
                    {
                        "phase_id": "prepare_target_namespace",
                        "objective": "Ensure the execution-time target namespace exists after placeholder substitution.",
                        "steps": [
                            {
                                "step_id": "prepare-target-namespace",
                                "title": f"Ensure namespace {target} exists",
                                "type": "namespace",
                                "commands": [
                                    f"kubectl get namespace {target}",
                                    f"kubectl apply -f deployment-artifacts/{namespace_rel}",
                                ],
                                "mutates_target": True,
                                "requires_human_approval": True,
                                "manifest_refs": [f"deployment-artifacts/{namespace_rel}"],
                            }
                        ],
                    },
                    {
                        "phase_id": "prepare_secret_placeholders",
                        "objective": "Confirm no generated Secret values are present.",
                        "steps": [
                            {
                                "step_id": "secret-values-not-generated",
                                "title": "Confirm no copied Secret values exist",
                                "type": "human_input",
                                "commands": [],
                                "mutates_target": False,
                                "requires_human_approval": False,
                                "expected_outcomes": ["Secret values are supplied only by humans through approved channels."],
                            }
                        ],
                    },
                    {
                        "phase_id": "install_helm_releases",
                        "objective": f"Install generated Helm release {generated_release_name} with dry-run first.",
                        "steps": [
                            {
                                "step_id": "helm-dry-run",
                                "title": "Render and dry-run Helm release",
                                "type": "helm",
                                "commands": [
                                    f"helm template {generated_release_name} deployment-artifacts/helm-chart/extracted/{chart['name']} --namespace {target} --values deployment-artifacts/{values_rel}",
                                    f"helm upgrade --install {generated_release_name} deployment-artifacts/helm-chart/extracted/{chart['name']} --namespace {target} --create-namespace --values deployment-artifacts/{values_rel} --dry-run",
                                ],
                                "mutates_target": False,
                                "requires_human_approval": False,
                                "values_refs": [f"deployment-artifacts/{values_rel}"],
                                "manifest_refs": [f"deployment-artifacts/{rendered_rel}"] if rendered_rel else [],
                            },
                            {
                                "step_id": "helm-approved-install",
                                "title": "Install Helm release after approval",
                                "type": "helm",
                                "commands": [
                                    f"helm upgrade --install {generated_release_name} deployment-artifacts/helm-chart/extracted/{chart['name']} --namespace {target} --create-namespace --values deployment-artifacts/{values_rel} --atomic --timeout 10m"
                                ],
                                "mutates_target": True,
                                "requires_human_approval": True,
                            },
                        ],
                    },
                    {
                        "phase_id": "apply_ingress",
                        "objective": "Apply generated ingress only after backend service exists.",
                        "steps": [
                            {
                                "step_id": "apply-generated-ingress",
                                "title": "Apply generated ingress",
                                "type": "kubectl",
                                "commands": [f"kubectl apply -f deployment-artifacts/{ingress_rel}"] if ingress_rel else [],
                                "mutates_target": bool(ingress_rel),
                                "requires_human_approval": bool(ingress_rel),
                                "manifest_refs": [f"deployment-artifacts/{ingress_rel}"] if ingress_rel else [],
                            }
                        ],
                    },
                    {
                        "phase_id": "validate",
                        "objective": "Validate substituted target namespace resources after approved execution.",
                        "steps": [
                            {
                                "step_id": "validate-target",
                                "title": "Validate target resources",
                                "type": "validation",
                                "commands": [
                                    f"kubectl get all -n {target}",
                                    f"helm status {generated_release_name} -n {target}",
                                ],
                                "mutates_target": False,
                                "requires_human_approval": False,
                            }
                        ],
                    },
                ],
                "source_namespace": source,
                "target_namespace_placeholder": target,
                "generated_release_name": generated_release_name,
            }
        }
        return yaml.safe_dump(plan, sort_keys=False)

    @staticmethod
    def _human_mop_markdown(
        *,
        source: str,
        target: str,
        run_id: str,
        bundle_id: str,
        timestamp: str,
        change_intent: str,
        target_environment: str,
        helm_release: str | None,
        implementation_window: str | None,
        analysis_depth: str,
        generated_release_name: str,
        chart: dict,
        artifact_index: dict,
        source_evidence_summary: str,
        limitations: list[str],
        validation: dict,
        recovery: dict,
        draft_markdown: str,
        warnings: list[str],
    ) -> str:
        inventory_rows = [
            f"| Helm chart | {chart.get('name')} {chart.get('version')} | Source: {chart.get('source')} |",
            f"| Values file | {', '.join(artifact_index.get('values') or [])} | Generated for `{generated_release_name}` |",
            f"| Kubernetes manifests | {len(artifact_index.get('kubernetes_manifests') or [])} | Namespace/ingress/raw deployables |",
            f"| CRDs | {len(artifact_index.get('crds') or [])} | Operator-reviewed before execution |",
            f"| Rendered manifests | {len(artifact_index.get('rendered_manifests') or [])} | Helm template output |",
        ]
        limitations_text = "\n".join(f"- {item}" for item in limitations or ["No additional limitations were reported by the draft writer."])
        warning_text = "\n".join(f"- {item}" for item in warnings or ["No bundle generation warnings."])
        return f"""# MoP: Namespace Recreation MoP - {source} to {target}

---

## Document Header

| Field | Value |
|---|---|
| **MoP Title** | Namespace Recreation MoP - {source} to {target} |
| **MoP ID** | {bundle_id} |
| **Version** | ESDA MoP artifact-bundle V1 |
| **Generator** | Ericsson Autonomous SRE and DevOps Agent |
| **Generated At** | {timestamp} |
| **Reviewed By** | TBD |
| **Change Ticket** | TBD |
| **Change Window** | {implementation_window or 'TBD'} |
| **Source Namespace** | {source} |
| **Target Namespace Placeholder** | {target} |
| **Target Environment** | {target_environment} |
| **Run ID** | {run_id} |
| **Generation Mode** | artifact generation only; no mutation |

---

## Change Summary

**What:** {change_intent}

**Why:** Generate a governed MoP and deployment artifact bundle that can recreate the source namespace footprint in `{target}` after human review.

**Impact:** This workflow generates documents, Helm values, rendered manifests, namespace/ingress manifests, and a zip archive only. It does not apply Kubernetes resources, execute Helm mutation, or copy Secret values.

| Category | Count | Notes |
|---|---:|---|
{chr(10).join(inventory_rows)}

## 1. Access & Environment Verification

1. Confirm target cluster context:

```bash
kubectl config current-context
```

2. Confirm source namespace visibility:

```bash
kubectl get namespace {source}
kubectl get all -n {source}
```

3. Confirm execution-time namespace has not been bound during generation:

```bash
kubectl get namespace {target} || true
```

4. Confirm Helm availability:

```bash
helm version
helm list -n {target}
```

## 2. Pre-change Backup

- Export non-secret source namespace state before any later execution workflow.
- Do not export Kubernetes Secret `data` or `stringData`.
- Keep generated bundle files attached to the change ticket.

## 3. Namespace Placeholder and Environment

- Source namespace: `{source}`
- Target namespace placeholder: `{target}`
- Generated release name: `{generated_release_name}`
- Environment type: `{target_environment}`
- Helm release hint: `{helm_release or 'Not supplied; inferred where evidence allowed'}`
- Analysis depth: `{analysis_depth}`

## 4. Scope and Assumptions

- Scope is limited to read-only MoP and deployment artifact bundle generation.
- No Kubernetes or Helm mutation is performed by this workflow.
- Human approval is required before any execution workflow uses this document.
- Generated resources use the `agent-ai` prefix to avoid collisions.
- Secret values are excluded and must be supplied through approved secure processes.

## 5. Source Evidence

- Kubernetes inspector status: `{_tool_status_text('k8s-inspector', artifact_index)}`
- Helm chart classification: `{chart.get('source')}`
- Chart: `{chart.get('name')}` version `{chart.get('version')}`
- Source evidence summary: {source_evidence_summary}

### Evidence Limitations and Warnings

{warning_text}

### Draft Writer Limitations

{limitations_text}

## 6. Deployment Artifact Inventory

- `artifact.json`
- `machine_execution_plan.yaml`
- `mop-{source}-to-{target}-{timestamp}.human-mop.md`
- `mop-{source}-to-{target}-{timestamp}.installation.md`
- `mop-{source}-to-{target}-{timestamp}.pdf`
- `deployment-artifacts/artifact-index.json`
- `deployment-artifacts/helm-commands.md`
- `deployment-artifacts/helm-values/values-{generated_release_name}.yaml`
- `deployment-artifacts/kubernetes-manifests/namespace-{target}.yaml`
- `deployment-artifacts.zip`
- `mop-bundle.zip`

## 7. Implementation Steps

> These steps are for a later governed execution workflow. They are not executed during MoP generation.

1. Review `artifact.json`, `machine_execution_plan.yaml`, and `deployment-artifacts/artifact-index.json`.
2. Run Helm render/dry-run commands from `deployment-artifacts/helm-commands.md`.
3. Confirm generated values and rendered manifests are safe for `{target}`.
4. Obtain human approval for any mutation.
5. Apply the namespace manifest if approved.
6. Install the Helm release with `helm upgrade --install {generated_release_name}` after dry-run success.
7. Apply generated ingress only after backend services exist.

## 8. Validation Steps

```bash
kubectl get all -n {target}
helm status {generated_release_name} -n {target}
kubectl get ingress -n {target}
```

Expected result: after MoP Execution substitutes the real target namespace, resources become visible and the Helm release reports healthy status.

## 9. Rollback Plan

1. Stop rollout if dry-run, readiness, or smoke validation fails.
2. If Helm mutation was approved and executed later, rollback with:

```bash
helm rollback {generated_release_name} -n {target}
```

3. If namespace creation was only for this change and cleanup is approved:

```bash
kubectl delete namespace {target}
```

## 10. Risk Assessment Matrix

| Risk | Level | Mitigation |
|---|---|---|
| Missing Secret values | Medium | Human supplies approved values; source Secrets are never copied. |
| Chart source incomplete | Medium | Fail closed or mark evidence-only bundle when chart source is unknown. |
| Ingress mismatch | Medium | Disable chart ingress and generate agent-prefixed ingress only when source ingress exists. |
| CRD impact | High | CRDs require operator review before any execution workflow applies them. |

## 11. Approval and Human Review Notes

- Review artifact metadata and deployment-artifact zip contents.
- Review values file and rendered manifests for namespace rewrite, prefixes, and redaction.
- Confirm no mutation command was executed during generation.
- Validation result: `{validation.get('message', 'validation result unavailable')}`
- Recovery action: `{recovery.get('action', 'continue')}`

## 12. Agent Activity and Safe Reasoning Summaries

The ESDA page records safe reasoning summaries and MCP/tool events in PostgreSQL. Ephemeral live working notes are not persisted.

## 13. Initial GPT Draft Reference

The following excerpt from the model draft is retained as supporting context and is superseded by this artifact bundle MoP:

```markdown
{draft_markdown[:3500]}
```
"""

    @staticmethod
    def _installation_markdown(
        *,
        source: str,
        target: str,
        run_id: str,
        timestamp: str,
        change_intent: str,
        target_environment: str,
        generated_release_name: str,
        chart: dict,
        artifact_index: dict,
        warnings: list[str],
    ) -> str:
        warnings_text = "\n".join(f"- {item}" for item in warnings or ["No warnings."])
        return f"""---
artifact_type: mop_installation_notes
source_namespace: {source}
target_namespace_placeholder: {target}
generated_release_name: {generated_release_name}
generated_at: {timestamp}
run_id: {run_id}
---

# Installation Notes: {source} to {target}

## Purpose

Provide operator guidance for reviewing and later executing the generated deployment artifact bundle for `{source}` into `{target}`.

## Execution Constraints

- Artifact generation performed no mutation.
- Dry-run/render is allowed.
- Human approval is required before applying namespace, Helm, ingress, CRDs, or any other resource.
- Kubernetes Secret values are excluded from all generated artifacts.

## Required Inputs

- Target cluster context.
- Execution-time target namespace supplied by MoP Execution.
- Approved secret values, if the chart requires them.
- Human approval/change ticket before mutation.

## Evidence Summary

- Change intent: {change_intent}
- Environment: {target_environment}
- Chart: {chart.get('name')} {chart.get('version')}
- Generated release: {generated_release_name}
- Values files: {', '.join(artifact_index.get('values') or [])}
- Rendered manifests: {', '.join(artifact_index.get('rendered_manifests') or []) or 'not generated'}

## Artifact Bundle Contents

```text
artifact.json
machine_execution_plan.yaml
mop-{source}-to-{target}-{timestamp}.human-mop.md
mop-{source}-to-{target}-{timestamp}.installation.md
mop-{source}-to-{target}-{timestamp}.pdf
deployment-artifacts/
  artifact-index.json
  helm-commands.md
  helm-chart/
  helm-values/
  kubernetes-manifests/
  rendered-manifests/
deployment-artifacts.zip
mop-bundle.zip
```

## Warnings

{warnings_text}

## Operator Flow

1. Inspect `artifact.json` and `deployment-artifacts/artifact-index.json`.
2. Download `mop-bundle.zip` for the complete run package, or unzip `deployment-artifacts.zip` for deployment-only files.
3. Review `helm-values/values-{generated_release_name}.yaml`.
4. Run the render and dry-run commands from `helm-commands.md`.
5. Request approval.
6. Execute only the approved commands.
7. Validate target resources.
"""

    @staticmethod
    def _artifact_metadata(
        *,
        run_id: str,
        user_id: str,
        source: str,
        target: str,
        timestamp: str,
        generated_release_name: str,
        change_intent: str,
        target_environment: str,
        helm_release: str | None,
        implementation_window: str | None,
        analysis_depth: str,
        model_profile: dict,
        classification: dict,
        plan: dict,
        tool_results: dict,
        validation: dict,
        recovery: dict,
        doc_refs: list[dict],
        artifact_index: dict,
        warnings: list[str],
    ) -> dict:
        return {
            "artifact_type": "bosgenesis_mop_bundle",
            "schema_version": "1.0",
            "run_id": run_id,
            "user_id": user_id,
            "source_namespace": source,
            "target_namespace_placeholder": target,
            "generated_release_name": generated_release_name,
            "generated_at": timestamp,
            "operation": "generate_mop_and_artifact_bundle_only",
            "mutation_allowed": False,
            "dry_run_allowed": True,
            "secret_copying_allowed": False,
            "change_intent": change_intent,
            "target_environment": target_environment,
            "helm_release": helm_release,
            "implementation_window": implementation_window,
            "analysis_depth": analysis_depth,
            "model_profile": model_profile,
            "classification": classification,
            "plan": plan,
            "tool_status": MopBundleBuilder._tool_status_summary(tool_results),
            "validation": validation,
            "recovery": recovery,
            "reference_documents": doc_refs,
            "artifact_index": artifact_index,
            "warnings": warnings,
            "guardrails": [
                "No mutation was performed.",
                "No source Secret values were copied.",
                "Generated names use an agent-ai prefix.",
                "Machine execution plan requires human approval before mutation.",
            ],
        }

    @staticmethod
    def _tool_status_summary(tool_results: dict | None) -> dict:
        summary = {}
        for name, result in (tool_results or {}).items():
            if isinstance(result, dict):
                summary[name] = result.get("status") or result.get("state") or "available"
            elif isinstance(result, list):
                summary[name] = f"items:{len(result)}"
            elif result is None:
                summary[name] = "missing"
            else:
                summary[name] = str(type(result).__name__)
        return summary

    @staticmethod
    def _validate_bundle(
        *,
        bundle_root: Path,
        deployment_root: Path,
        chart: dict,
        chart_files: dict,
        rendered_rel: str | None,
        ingress_rel: str | None,
        warnings: list[str],
    ) -> dict:
        checks = {
            "artifact_json": (bundle_root / "artifact.json").exists(),
            "machine_execution_plan": (bundle_root / "machine_execution_plan.yaml").exists(),
            "deployment_artifacts_zip": (bundle_root / "deployment-artifacts.zip").exists(),
            "mop_bundle_zip": (bundle_root / "mop-bundle.zip").exists(),
            "artifact_index": (deployment_root / "artifact-index.json").exists(),
            "helm_commands": (deployment_root / "helm-commands.md").exists(),
            "namespace_manifest": any((deployment_root / "kubernetes-manifests").glob("namespace-*.yaml")),
            "ingress_policy": bool(ingress_rel) == (deployment_root / str(ingress_rel or "")).exists()
            if ingress_rel
            else True,
            "rendered_manifest": bool(rendered_rel and (deployment_root / rendered_rel).exists()),
        }
        if chart.get("helm_managed"):
            checks["helm_chart_package"] = bool(chart_files.get("package_rel"))
            checks["helm_chart_extracted"] = bool(chart_files.get("extracted_chart_rel"))
        valid = all(value for key, value in checks.items() if key not in {"rendered_manifest"}) and (
            checks["rendered_manifest"] or not chart.get("helm_managed")
        )
        return {
            "valid": valid,
            "message": "MoP bundle generated with required files." if valid else "MoP bundle generated with gaps; review warnings.",
            "checks": checks,
            "warnings": warnings,
        }

    @staticmethod
    def _zip_complete_bundle(bundle_root: Path, zip_path: Path) -> None:
        zip_resolved = zip_path.resolve()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle_root.rglob("*")):
                if path.is_file() and path.resolve() != zip_resolved:
                    archive.write(path, path.relative_to(bundle_root).as_posix())

    @staticmethod
    def _zip_deployment_artifacts(deployment_root: Path, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(deployment_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(deployment_root).as_posix())

    @staticmethod
    def _source_has_ingress(source: str, k8s_result: dict) -> bool:
        if source == "signoz":
            return True
        text = json.dumps(k8s_result, default=str).lower()
        return '"ingress"' in text or "ingresses" in text

    @staticmethod
    def _first_helm_release(helm_result: dict) -> dict | None:
        payload = helm_result.get("output") if isinstance(helm_result, dict) else None
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            for key in ("release", "current_release"):
                if isinstance(result.get(key), dict):
                    return result[key]
            for key in ("releases", "output"):
                releases = result.get(key)
                if isinstance(releases, list) and releases:
                    return releases[0] if isinstance(releases[0], dict) else None
            if result.get("name") or result.get("release_name"):
                return result
        if isinstance(result, list) and result:
            return result[0] if isinstance(result[0], dict) else None
        return None

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, content: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2, sort_keys=False), encoding="utf-8")

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^a-z0-9.-]+", "-", str(value or "").strip().lower())
    return clean.strip(".-") or "namespace"


def _safe_token(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return clean.strip(".-") or "mop"


def _split_chart_ref(chart_ref: str) -> tuple[str | None, str | None]:
    if not chart_ref:
        return None, None
    name = chart_ref.rsplit("/", 1)[-1]
    match = re.match(r"(?P<chart>.+)-(?P<version>\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)$", name)
    if not match:
        return name or None, None
    return match.group("chart"), match.group("version")


def _tool_status_text(_label: str, artifact_index: dict) -> str:
    warnings = artifact_index.get("warnings") or []
    return "limited" if warnings else "available"
