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

import inspect

import pytest
from flask import Blueprint
from flask_appbuilder import BaseView

from airflow.hooks.base import BaseHook
from airflow.models.baseoperator import BaseOperatorLink
from airflow.plugins_manager import AirflowPlugin
from airflow.security import permissions
from airflow.ti_deps.deps.base_ti_dep import BaseTIDep
from airflow.timetables.base import Timetable
from airflow.utils.module_loading import qualname
from tests.test_utils.api_connexion_utils import assert_401, create_user, delete_user
from tests.test_utils.config import conf_vars
from tests.test_utils.mock_plugins import mock_plugin_manager


class PluginHook(BaseHook):
    ...


def plugin_macro():
    ...


class MockOperatorLink(BaseOperatorLink):
    name = "mock_operator_link"

    def get_link(self, operator, *, ti_key) -> str:
        return "mock_operator_link"


bp = Blueprint("mock_blueprint", __name__, url_prefix="/mock_blueprint")


class MockView(BaseView):
    ...


mockview = MockView()

appbuilder_menu_items = {
    "name": "mock_plugin",
    "href": "https://example.com",
}


class CustomTIDep(BaseTIDep):
    pass


ti_dep = CustomTIDep()


class CustomTimetable(Timetable):
    def infer_manual_data_interval(self, *, run_after):
        pass

    def next_dagrun_info(
        self,
        *,
        last_automated_data_interval,
        restriction,
    ):
        pass


class MyCustomListener:
    pass


class MockPlugin(AirflowPlugin):
    name = "mock_plugin"
    flask_blueprints = [bp]
    appbuilder_views = [{"view": mockview}]
    appbuilder_menu_items = [appbuilder_menu_items]
    global_operator_extra_links = [MockOperatorLink()]
    operator_extra_links = [MockOperatorLink()]
    hooks = [PluginHook]
    macros = [plugin_macro]
    ti_deps = [ti_dep]
    timetables = [CustomTimetable]
    listeners = [pytest, MyCustomListener()]  # using pytest here because we need a module(just for test)


@pytest.fixture(scope="module")
def configured_app(minimal_app_for_api):
    app = minimal_app_for_api
    create_user(
        app,  # type: ignore
        username="test",
        role_name="Test",
        permissions=[(permissions.ACTION_CAN_READ, permissions.RESOURCE_PLUGIN)],
    )
    create_user(app, username="test_no_permissions", role_name="TestNoPermissions")  # type: ignore

    yield app

    delete_user(app, username="test")  # type: ignore
    delete_user(app, username="test_no_permissions")  # type: ignore


class TestPluginsEndpoint:
    @pytest.fixture(autouse=True)
    def setup_attrs(self, configured_app) -> None:
        """
        Setup For XCom endpoint TC
        """
        self.app = configured_app
        self.client = self.app.test_client()  # type:ignore


class TestGetPlugins(TestPluginsEndpoint):
    def test_get_plugins_return_200(self):
        mock_plugin = MockPlugin()
        mock_plugin.name = "test_plugin"
        with mock_plugin_manager(plugins=[mock_plugin]):
            response = self.client.get("api/v1/plugins", environ_overrides={"REMOTE_USER": "test"})
        assert response.status_code == 200
        assert response.json == {
            "plugins": [
                {
                    "appbuilder_menu_items": [appbuilder_menu_items],
                    "appbuilder_views": [{"view": qualname(MockView)}],
                    "executors": [],
                    "flask_blueprints": [
                        f"<{qualname(bp.__class__)}: name={bp.name!r} import_name={bp.import_name!r}>"
                    ],
                    "global_operator_extra_links": [f"<{qualname(MockOperatorLink().__class__)} object>"],
                    "hooks": [qualname(PluginHook)],
                    "macros": [qualname(plugin_macro)],
                    "operator_extra_links": [f"<{qualname(MockOperatorLink().__class__)} object>"],
                    "source": None,
                    "name": "test_plugin",
                    "timetables": [qualname(CustomTimetable)],
                    "ti_deps": [str(ti_dep)],
                    "listeners": [
                        d.__name__ if inspect.ismodule(d) else qualname(d)
                        for d in [pytest, MyCustomListener()]
                    ],
                }
            ],
            "total_entries": 1,
        }

    def test_get_plugins_works_with_more_plugins(self):
        mock_plugin = AirflowPlugin()
        mock_plugin.name = "test_plugin"
        mock_plugin_2 = AirflowPlugin()
        mock_plugin_2.name = "test_plugin2"
        with mock_plugin_manager(plugins=[mock_plugin, mock_plugin_2]):
            response = self.client.get("api/v1/plugins", environ_overrides={"REMOTE_USER": "test"})
        assert response.status_code == 200
        assert response.json["total_entries"] == 2

    def test_get_plugins_return_200_if_no_plugins(self):
        with mock_plugin_manager(plugins=[]):
            response = self.client.get("api/v1/plugins", environ_overrides={"REMOTE_USER": "test"})
        assert response.status_code == 200

    def test_should_raises_401_unauthenticated(self):
        response = self.client.get("/api/v1/plugins")

        assert_401(response)

    def test_should_raise_403_forbidden(self):
        response = self.client.get(
            "/api/v1/plugins", environ_overrides={"REMOTE_USER": "test_no_permissions"}
        )
        assert response.status_code == 403


class TestGetPluginsPagination(TestPluginsEndpoint):
    @pytest.mark.parametrize(
        "url, expected_plugin_names",
        [
            ("/api/v1/plugins?limit=1", ["TEST_PLUGIN_1"]),
            ("/api/v1/plugins?limit=2", ["TEST_PLUGIN_1", "TEST_PLUGIN_2"]),
            (
                "/api/v1/plugins?offset=5",
                [
                    "TEST_PLUGIN_6",
                    "TEST_PLUGIN_7",
                    "TEST_PLUGIN_8",
                    "TEST_PLUGIN_9",
                    "TEST_PLUGIN_10",
                ],
            ),
            (
                "/api/v1/plugins?offset=0",
                [
                    "TEST_PLUGIN_1",
                    "TEST_PLUGIN_2",
                    "TEST_PLUGIN_3",
                    "TEST_PLUGIN_4",
                    "TEST_PLUGIN_5",
                    "TEST_PLUGIN_6",
                    "TEST_PLUGIN_7",
                    "TEST_PLUGIN_8",
                    "TEST_PLUGIN_9",
                    "TEST_PLUGIN_10",
                ],
            ),
            ("/api/v1/plugins?limit=1&offset=5", ["TEST_PLUGIN_6"]),
            ("/api/v1/plugins?limit=1&offset=1", ["TEST_PLUGIN_2"]),
            (
                "/api/v1/plugins?limit=2&offset=2",
                ["TEST_PLUGIN_3", "TEST_PLUGIN_4"],
            ),
        ],
    )
    def test_handle_limit_offset(self, url, expected_plugin_names):
        plugins = self._create_plugins(10)
        with mock_plugin_manager(plugins=plugins):
            response = self.client.get(url, environ_overrides={"REMOTE_USER": "test"})
        assert response.status_code == 200
        assert response.json["total_entries"] == 10
        plugin_names = [plugin["name"] for plugin in response.json["plugins"] if plugin]
        assert plugin_names == expected_plugin_names

    def test_should_respect_page_size_limit_default(self):
        plugins = self._create_plugins(200)
        with mock_plugin_manager(plugins=plugins):
            response = self.client.get("/api/v1/plugins", environ_overrides={"REMOTE_USER": "test"})
        assert response.status_code == 200
        assert response.json["total_entries"] == 200
        assert len(response.json["plugins"]) == 100

    def test_limit_of_zero_should_return_default(self):
        plugins = self._create_plugins(200)
        with mock_plugin_manager(plugins=plugins):
            response = self.client.get("/api/v1/plugins?limit=0", environ_overrides={"REMOTE_USER": "test"})
        assert response.status_code == 200
        assert response.json["total_entries"] == 200
        assert len(response.json["plugins"]) == 100

    @conf_vars({("api", "maximum_page_limit"): "150"})
    def test_should_return_conf_max_if_req_max_above_conf(self):
        plugins = self._create_plugins(200)
        with mock_plugin_manager(plugins=plugins):
            response = self.client.get("/api/v1/plugins?limit=180", environ_overrides={"REMOTE_USER": "test"})
        assert response.status_code == 200
        assert len(response.json["plugins"]) == 150

    def _create_plugins(self, count):
        plugins = []
        for i in range(1, count + 1):
            mock_plugin = AirflowPlugin()
            mock_plugin.name = f"TEST_PLUGIN_{i}"
            plugins.append(mock_plugin)
        return plugins
