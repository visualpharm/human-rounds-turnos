"""Static contract for the bounded Tier 6 Human Rounds Helm chart.

The chart has not been rendered or installed: Helm CLI is unavailable in this
development environment. These checks therefore parse the chart's plain YAML
with PyYAML and pin the source-level contract that must survive later chart
edits. They make no cluster, registry, database, or network call.
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "human-rounds"


def _read(relative_path: str) -> str:
    return (CHART / relative_path).read_text(encoding="utf-8")


class TestHelmChartContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart = yaml.safe_load(_read("Chart.yaml"))
        cls.values = yaml.safe_load(_read("values.yaml"))
        cls.deployment = _read("templates/deployment.yaml")
        cls.service = _read("templates/service.yaml")
        cls.pvc = _read("templates/pvc.yaml")
        cls.helpers = _read("templates/_helpers.tpl")
        cls.application_env_guard = _read("templates/application-env-guard.yaml")
        cls.postgresql_scaffold = _read("templates/postgresql-scaffold.yaml")
        cls.readme = _read("README.md")

    def test_chart_metadata_is_a_helm_v2_application(self):
        self.assertEqual(self.chart["apiVersion"], "v2")
        self.assertEqual(self.chart["name"], "human-rounds")
        self.assertEqual(self.chart["type"], "application")

    def test_default_image_is_ghcr_with_tag_and_digest_override(self):
        image = self.values["image"]
        self.assertEqual(image["repository"], "ghcr.io/visualpharm/sana-turnos")
        self.assertTrue(image["tag"])
        self.assertEqual(image["digest"], "")
        self.assertIn('printf "%s@%s" .Values.image.repository .Values.image.digest',
                      self.helpers)
        self.assertIn('printf "%s:%s" .Values.image.repository .Values.image.tag',
                      self.helpers)
        self.assertLess(self.helpers.index('if .Values.image.digest'),
                        self.helpers.index('printf "%s@%s"'))

    def test_application_port_and_probes_use_the_real_health_contract(self):
        self.assertEqual(self.values["service"]["port"], 7700)
        self.assertIn("containerPort: 7700", self.deployment)
        self.assertNotIn("containerPort: {{ .Values.service.port }}", self.deployment)
        self.assertIn('- name: PORT\n              value: "7700"', self.deployment)
        self.assertIn("port: {{ .Values.service.port }}", self.service)
        self.assertIn("targetPort: http", self.service)
        self.assertIn("path: /health", self.deployment)
        self.assertIn("path: /ready", self.deployment)
        self.assertIn("port: http", self.deployment)

    def test_service_port_can_change_without_changing_the_app_port(self):
        alternative_port = 8080
        self.assertNotEqual(alternative_port, self.values["service"]["port"])
        rendered_service_port = self.service.replace(
            "{{ .Values.service.port }}", str(alternative_port))
        self.assertIn("port: 8080", rendered_service_port)
        self.assertIn("targetPort: http", rendered_service_port)
        self.assertIn("containerPort: 7700", self.deployment)

    def test_app_data_is_persistent_by_default(self):
        persistence = self.values["persistence"]
        self.assertTrue(persistence["enabled"])
        self.assertIn("PersistentVolumeClaim", self.pvc)
        self.assertIn("mountPath: /app/data", self.deployment)
        self.assertIn("persistentVolumeClaim:", self.deployment)

    def test_root_owned_image_logs_have_a_writable_ephemeral_mount(self):
        self.assertIn("- name: app-logs", self.deployment)
        self.assertIn("mountPath: /app/logs", self.deployment)
        self.assertIn("- name: app-logs\n          emptyDir: {}", self.deployment)

    def test_database_url_comes_from_an_operator_secret(self):
        database = self.values["database"]
        self.assertTrue(database["existingSecret"])
        self.assertEqual(database["existingSecretKey"], "DATABASE_URL")
        self.assertIn("name: DATABASE_URL", self.deployment)
        self.assertIn("secretKeyRef:", self.deployment)
        self.assertIn("database.existingSecret", self.deployment)
        self.assertNotIn("kind: Secret", self.deployment)

    def test_application_env_is_a_generic_pass_through_map(self):
        self.assertEqual(self.values["applicationEnv"], {})
        self.assertIn("range $name, $value := .Values.applicationEnv", self.deployment)
        self.assertIn("value: {{ $value | toString | quote }}", self.deployment)
        self.assertIn("pass-through map", self.readme)

    def test_application_env_cannot_shadow_database_or_process_port(self):
        self.assertIn('hasKey .Values.applicationEnv "DATABASE_URL"',
                      self.application_env_guard)
        self.assertIn('hasKey .Values.applicationEnv "PORT"',
                      self.application_env_guard)
        self.assertIn("database.existingSecret", self.application_env_guard)
        self.assertIn("container port 7700", self.application_env_guard)

    def test_in_chart_postgres_is_disabled_and_honestly_fails_if_enabled(self):
        self.assertFalse(self.values["postgresql"]["enabled"])
        self.assertIn("scaffolding-only", self.postgresql_scaffold)
        self.assertIn("provide an externally managed DATABASE_URL Secret",
                      self.postgresql_scaffold)
        self.assertIn("scaffold", self.readme.lower())

    def test_readme_records_the_kind_install_and_rollout_proof_boundary(self):
        lowered = self.readme.lower()
        self.assertIn("helm install --wait", lowered)
        self.assertIn("helm upgrade --wait", lowered)
        self.assertIn("/health", lowered)
        self.assertIn("/ready", lowered)
        self.assertIn("/setup", lowered)
        self.assertIn("every application pod", lowered)
        self.assertIn("not evidence that the repository's ghcr workflow", lowered)
        self.assertIn("external postgresql boundary", lowered)


if __name__ == "__main__":
    unittest.main()
