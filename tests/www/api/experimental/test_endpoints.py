#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import json
import os
import re
from datetime import timedelta
from unittest import mock
from urllib.parse import quote_plus

import pytest

from airflow import settings
from airflow.api.common.experimental.trigger_dag import trigger_dag
from airflow.models import DagBag, DagRun, Pool, TaskInstance
from airflow.models.serialized_dag import SerializedDagModel
from airflow.settings import Session
from airflow.utils.timezone import datetime, parse as parse_datetime, utcnow
from airflow.version import version
from tests.test_utils.db import clear_db_pools

ROOT_FOLDER = os.path.realpath(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir, os.pardir, os.pardir, os.pardir)
)


@pytest.fixture(scope="module")
def configured_session():
    settings.configure_orm()
    return Session


class TestBase:
    @pytest.fixture(autouse=True)
    def _setup_attrs_base(self, experiemental_api_app, configured_session):
        self.app = experiemental_api_app
        self.appbuilder = self.app.appbuilder
        self.client = self.app.test_client()
        self.session = configured_session

    def assert_deprecated(self, resp):
        assert "true" == resp.headers["Deprecation"]
        assert re.search(
            r"<.+/upgrading-to-2.html#migration-guide-from-experimental-api-to-stable-api-v1>; "
            'rel="deprecation"; type="text/html"',
            resp.headers["Link"],
        )


class TestApiExperimental(TestBase):
    @pytest.fixture(scope="class", autouse=True)
    def _populate_db(self):
        session = Session()
        session.query(DagRun).delete()
        session.query(TaskInstance).delete()
        session.commit()
        session.close()

        dagbag = DagBag(include_examples=True)
        for dag in dagbag.dags.values():
            dag.sync_to_db()
            SerializedDagModel.write_dag(dag)

    @pytest.fixture(autouse=True)
    def _reset_db(self):
        session = Session()
        session.query(DagRun).delete()
        session.query(TaskInstance).delete()
        session.commit()
        session.close()

    def test_info(self):
        url = "/api/experimental/info"

        resp_raw = self.client.get(url)
        resp = json.loads(resp_raw.data.decode("utf-8"))

        assert version == resp["version"]
        self.assert_deprecated(resp_raw)

    def test_task_info(self):
        url_template = "/api/experimental/dags/{}/tasks/{}"

        response = self.client.get(url_template.format("example_bash_operator", "runme_0"))
        self.assert_deprecated(response)

        assert '"email"' in response.data.decode("utf-8")
        assert "error" not in response.data.decode("utf-8")
        assert 200 == response.status_code

        response = self.client.get(url_template.format("example_bash_operator", "does-not-exist"))
        assert "error" in response.data.decode("utf-8")
        assert 404 == response.status_code

        response = self.client.get(url_template.format("does-not-exist", "does-not-exist"))
        assert "error" in response.data.decode("utf-8")
        assert 404 == response.status_code

    def test_get_dag_code(self):
        url_template = "/api/experimental/dags/{}/code"

        response = self.client.get(url_template.format("example_bash_operator"))
        self.assert_deprecated(response)
        assert "BashOperator(" in response.data.decode("utf-8")
        assert 200 == response.status_code

        response = self.client.get(url_template.format("xyz"))
        assert 404 == response.status_code

    def test_dag_paused(self):
        pause_url_template = "/api/experimental/dags/{}/paused/{}"
        paused_url_template = "/api/experimental/dags/{}/paused"
        paused_url = paused_url_template.format("example_bash_operator")

        response = self.client.get(pause_url_template.format("example_bash_operator", "true"))
        self.assert_deprecated(response)
        assert "ok" in response.data.decode("utf-8")
        assert 200 == response.status_code

        paused_response = self.client.get(paused_url)

        assert 200 == paused_response.status_code
        assert {"is_paused": True} == paused_response.json

        response = self.client.get(pause_url_template.format("example_bash_operator", "false"))
        assert "ok" in response.data.decode("utf-8")
        assert 200 == response.status_code

        paused_response = self.client.get(paused_url)

        assert 200 == paused_response.status_code
        assert {"is_paused": False} == paused_response.json

    def test_trigger_dag(self):
        url_template = "/api/experimental/dags/{}/dag_runs"
        run_id = "my_run" + utcnow().isoformat()

        # Test error for nonexistent dag
        response = self.client.post(
            url_template.format("does_not_exist_dag"), data=json.dumps({}), content_type="application/json"
        )
        assert 404 == response.status_code

        # Test error for bad conf data
        response = self.client.post(
            url_template.format("example_bash_operator"),
            data=json.dumps({"conf": "This is a string not a dict"}),
            content_type="application/json",
        )
        assert 400 == response.status_code

        # Test OK case
        response = self.client.post(
            url_template.format("example_bash_operator"),
            data=json.dumps({"run_id": run_id, "conf": {"param": "value"}}),
            content_type="application/json",
        )
        self.assert_deprecated(response)

        assert 200 == response.status_code
        response_execution_date = parse_datetime(json.loads(response.data.decode("utf-8"))["execution_date"])
        assert 0 == response_execution_date.microsecond

        # Check execution_date is correct
        response = json.loads(response.data.decode("utf-8"))
        dagbag = DagBag()
        dag = dagbag.get_dag("example_bash_operator")
        dag_run = dag.get_dagrun(response_execution_date)
        dag_run_id = dag_run.run_id
        assert run_id == dag_run_id
        assert dag_run_id == response["run_id"]

    def test_trigger_dag_for_date(self):
        url_template = "/api/experimental/dags/{}/dag_runs"
        dag_id = "example_bash_operator"
        execution_date = utcnow() + timedelta(hours=1)
        datetime_string = execution_date.isoformat()

        # Test correct execution with execution date
        response = self.client.post(
            url_template.format(dag_id),
            data=json.dumps({"execution_date": datetime_string}),
            content_type="application/json",
        )
        self.assert_deprecated(response)
        assert 200 == response.status_code
        assert datetime_string == json.loads(response.data.decode("utf-8"))["execution_date"]

        dagbag = DagBag()
        dag = dagbag.get_dag(dag_id)
        dag_run = dag.get_dagrun(execution_date)
        assert dag_run, f"Dag Run not found for execution date {execution_date}"

        # Test correct execution with execution date and microseconds replaced
        response = self.client.post(
            url_template.format(dag_id),
            data=json.dumps({"execution_date": datetime_string, "replace_microseconds": "true"}),
            content_type="application/json",
        )
        assert 200 == response.status_code
        response_execution_date = parse_datetime(json.loads(response.data.decode("utf-8"))["execution_date"])
        assert 0 == response_execution_date.microsecond

        dagbag = DagBag()
        dag = dagbag.get_dag(dag_id)
        dag_run = dag.get_dagrun(response_execution_date)
        assert dag_run, f"Dag Run not found for execution date {execution_date}"

        # Test error for nonexistent dag
        response = self.client.post(
            url_template.format("does_not_exist_dag"),
            data=json.dumps({"execution_date": datetime_string}),
            content_type="application/json",
        )
        assert 404 == response.status_code

        # Test error for bad datetime format
        response = self.client.post(
            url_template.format(dag_id),
            data=json.dumps({"execution_date": "not_a_datetime"}),
            content_type="application/json",
        )
        assert 400 == response.status_code

    def test_task_instance_info(self):
        url_template = "/api/experimental/dags/{}/dag_runs/{}/tasks/{}"
        dag_id = "example_bash_operator"
        task_id = "also_run_this"
        execution_date = utcnow().replace(microsecond=0)
        datetime_string = quote_plus(execution_date.isoformat())
        wrong_datetime_string = quote_plus(datetime(1990, 1, 1, 1, 1, 1).isoformat())

        # Create DagRun
        trigger_dag(dag_id=dag_id, run_id="test_task_instance_info_run", execution_date=execution_date)

        # Test Correct execution
        response = self.client.get(url_template.format(dag_id, datetime_string, task_id))
        self.assert_deprecated(response)
        assert 200 == response.status_code
        assert "state" in response.data.decode("utf-8")
        assert "error" not in response.data.decode("utf-8")

        # Test error for nonexistent dag
        response = self.client.get(
            url_template.format("does_not_exist_dag", datetime_string, task_id),
        )
        assert 404 == response.status_code
        assert "error" in response.data.decode("utf-8")

        # Test error for nonexistent task
        response = self.client.get(url_template.format(dag_id, datetime_string, "does_not_exist_task"))
        assert 404 == response.status_code
        assert "error" in response.data.decode("utf-8")

        # Test error for nonexistent dag run (wrong execution_date)
        response = self.client.get(url_template.format(dag_id, wrong_datetime_string, task_id))
        assert 404 == response.status_code
        assert "error" in response.data.decode("utf-8")

        # Test error for bad datetime format
        response = self.client.get(url_template.format(dag_id, "not_a_datetime", task_id))
        assert 400 == response.status_code
        assert "error" in response.data.decode("utf-8")

    def test_dagrun_status(self):
        url_template = "/api/experimental/dags/{}/dag_runs/{}"
        dag_id = "example_bash_operator"
        execution_date = utcnow().replace(microsecond=0)
        datetime_string = quote_plus(execution_date.isoformat())
        wrong_datetime_string = quote_plus(datetime(1990, 1, 1, 1, 1, 1).isoformat())

        # Create DagRun
        trigger_dag(dag_id=dag_id, run_id="test_task_instance_info_run", execution_date=execution_date)

        # Test Correct execution
        response = self.client.get(url_template.format(dag_id, datetime_string))
        self.assert_deprecated(response)
        assert 200 == response.status_code
        assert "state" in response.data.decode("utf-8")
        assert "error" not in response.data.decode("utf-8")

        # Test error for nonexistent dag
        response = self.client.get(
            url_template.format("does_not_exist_dag", datetime_string),
        )
        assert 404 == response.status_code
        assert "error" in response.data.decode("utf-8")

        # Test error for nonexistent dag run (wrong execution_date)
        response = self.client.get(url_template.format(dag_id, wrong_datetime_string))
        assert 404 == response.status_code
        assert "error" in response.data.decode("utf-8")

        # Test error for bad datetime format
        response = self.client.get(url_template.format(dag_id, "not_a_datetime"))
        assert 400 == response.status_code
        assert "error" in response.data.decode("utf-8")


class TestLineageApiExperimental(TestBase):
    PAPERMILL_EXAMPLE_DAGS = os.path.join(ROOT_FOLDER, "tests", "system", "providers", "papermill")

    @pytest.fixture(scope="class", autouse=True)
    def _populate_db(self):
        session = Session()
        session.query(DagRun).delete()
        session.query(TaskInstance).delete()
        session.commit()
        session.close()

        dagbag = DagBag(
            include_examples=True,
            dag_folder=self.PAPERMILL_EXAMPLE_DAGS,
        )
        for dag in dagbag.dags.values():
            dag.sync_to_db()
            SerializedDagModel.write_dag(dag)

    @mock.patch("airflow.settings.DAGS_FOLDER", PAPERMILL_EXAMPLE_DAGS)
    def test_lineage_info(self):
        url_template = "/api/experimental/lineage/{}/{}"
        dag_id = "example_papermill_operator"
        execution_date = utcnow().replace(microsecond=0)
        datetime_string = quote_plus(execution_date.isoformat())
        wrong_datetime_string = quote_plus(datetime(1990, 1, 1, 1, 1, 1).isoformat())

        # create DagRun
        trigger_dag(dag_id=dag_id, run_id="test_lineage_info_run", execution_date=execution_date)

        # test correct execution
        response = self.client.get(url_template.format(dag_id, datetime_string))
        self.assert_deprecated(response)
        assert 200 == response.status_code
        assert "task_ids" in response.data.decode("utf-8")
        assert "error" not in response.data.decode("utf-8")

        # Test error for nonexistent dag
        response = self.client.get(
            url_template.format("does_not_exist_dag", datetime_string),
        )
        assert 404 == response.status_code
        assert "error" in response.data.decode("utf-8")

        # Test error for nonexistent dag run (wrong execution_date)
        response = self.client.get(url_template.format(dag_id, wrong_datetime_string))
        assert 404 == response.status_code
        assert "error" in response.data.decode("utf-8")

        # Test error for bad datetime format
        response = self.client.get(url_template.format(dag_id, "not_a_datetime"))
        assert 400 == response.status_code
        assert "error" in response.data.decode("utf-8")


class TestPoolApiExperimental(TestBase):
    USER_POOL_COUNT = 2
    TOTAL_POOL_COUNT = USER_POOL_COUNT + 1  # including default_pool

    @pytest.fixture(autouse=True)
    def _setup_attrs(self, _setup_attrs_base):
        clear_db_pools()
        self.pools = [Pool.get_default_pool()]
        for i in range(self.USER_POOL_COUNT):
            name = f"experimental_{i + 1}"
            pool = Pool(
                pool=name,
                slots=i,
                description=name,
                include_deferred=False,
            )
            self.session.add(pool)
            self.pools.append(pool)
        self.session.commit()
        self.pool = self.pools[-1]

    def _get_pool_count(self):
        response = self.client.get("/api/experimental/pools")
        assert response.status_code == 200
        return len(json.loads(response.data.decode("utf-8")))

    def test_get_pool(self):
        response = self.client.get(
            f"/api/experimental/pools/{self.pool.pool}",
        )
        self.assert_deprecated(response)
        assert response.status_code == 200
        assert json.loads(response.data.decode("utf-8")) == self.pool.to_json()

    def test_get_pool_non_existing(self):
        response = self.client.get("/api/experimental/pools/foo")
        assert response.status_code == 404
        assert json.loads(response.data.decode("utf-8"))["error"] == "Pool 'foo' doesn't exist"

    def test_get_pools(self):
        response = self.client.get("/api/experimental/pools")
        self.assert_deprecated(response)
        assert response.status_code == 200
        pools = json.loads(response.data.decode("utf-8"))
        assert len(pools) == self.TOTAL_POOL_COUNT
        for i, pool in enumerate(sorted(pools, key=lambda p: p["pool"])):
            assert pool == self.pools[i].to_json()

    def test_create_pool(self):
        response = self.client.post(
            "/api/experimental/pools",
            data=json.dumps(
                {
                    "name": "foo",
                    "slots": 1,
                    "description": "",
                }
            ),
            content_type="application/json",
        )
        self.assert_deprecated(response)
        assert response.status_code == 200
        pool = json.loads(response.data.decode("utf-8"))
        assert pool["pool"] == "foo"
        assert pool["slots"] == 1
        assert pool["description"] == ""
        assert pool["include_deferred"] is False
        assert self._get_pool_count() == self.TOTAL_POOL_COUNT + 1

    def test_create_pool_with_bad_name(self):
        for name in ("", "    "):
            response = self.client.post(
                "/api/experimental/pools",
                data=json.dumps(
                    {
                        "name": name,
                        "slots": 1,
                        "description": "",
                    }
                ),
                content_type="application/json",
            )
            assert response.status_code == 400
            assert json.loads(response.data.decode("utf-8"))["error"] == "Pool name shouldn't be empty"
        assert self._get_pool_count() == self.TOTAL_POOL_COUNT

    def test_delete_pool(self):
        response = self.client.delete(
            f"/api/experimental/pools/{self.pool.pool}",
        )
        self.assert_deprecated(response)
        assert response.status_code == 200
        assert json.loads(response.data.decode("utf-8")) == self.pool.to_json()
        assert self._get_pool_count() == self.TOTAL_POOL_COUNT - 1

    def test_delete_pool_non_existing(self):
        response = self.client.delete(
            "/api/experimental/pools/foo",
        )
        assert response.status_code == 404
        assert json.loads(response.data.decode("utf-8"))["error"] == "Pool 'foo' doesn't exist"

    def test_delete_default_pool(self):
        clear_db_pools()
        response = self.client.delete(
            "/api/experimental/pools/default_pool",
        )
        assert response.status_code == 400
        assert json.loads(response.data.decode("utf-8"))["error"] == "default_pool cannot be deleted"
