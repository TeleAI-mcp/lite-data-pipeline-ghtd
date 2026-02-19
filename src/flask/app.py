# Copyright (c) 2010-2024 Pallets
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#
#     * Redistributions in binary form must reproduce the above
#       copyright notice, this list of conditions and the following disclaimer
#       in the documentation and/or other materials provided with the
#       distribution.
#
#     * Neither the name of the copyright holder nor the names of its
#       contributors may be used to endorse or promote products derived from
#       this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""The flask object implements a WSGI application and acts as the central
object.  It is passed the name of the module or package of the
application.  Once it is created it will act as a central registry for
the view functions, the URL rules, template configuration and much more.

The name of the package is used to resolve resources from inside the
package or the folder the module is contained in depending on if the
package parameter resolves to an actual python package (a folder with
an :file:`__init__.py` file inside) or a standard module (just a single
file).
"""
from __future__ import annotations

import os
import sys
import typing as t
from datetime import timedelta

from . import cli
from . import typing as flask_typing
from .config import Config
from .ctx import AppContext
from .ctx import RequestContext
from .globals import _app_ctx_stack
from .globals import request_ctx
from .helpers import get_debug_flag
from .helpers import get_env
from .helpers import get_flashed_messages
from .helpers import url_for
from .json.provider import DefaultJSONProvider
from .json.tag import JSONTag
from .sessions import SessionInterface
from .signals import appcontext_tearing_down
from .signals import appcontext_pushed
from .signals import appcontext_popped
from .signals import before_render_template
from .signals import message_flashed
from .signals import request_finished
from .signals import request_started
from .signals import request_tearing_down
from .templating import DispatchingJinjaLoader
from .templating import Environment
from .testing import FlaskClient
from .testing import FlaskCliRunner
from .typing import AfterRequestCallable
from .typing import AppOrBlueprintKey
from .typing import BeforeRequestCallable
from .typing import ErrorHandlerCallable
from .typing import TemplateFilterCallable
from .typing import TemplateGlobalCallable
from .typing import TemplateTestCallable
from .typing import TeardownCallable
from .typing import URLDefaultCallable
from .typing import URLValuePreprocessorCallable
from .wrappers import Request
from .wrappers import Response

if t.TYPE_CHECKING:
    from werkzeug.routing import Rule

F = t.TypeVar("F", bound=t.Callable[..., t.Any])
T_route = t.TypeVar("T_route", bound=t.Callable[..., t.Any])
T_after_request = t.TypeVar("T_after_request", bound=AfterRequestCallable)
T_before_request = t.TypeVar("T_before_request", bound=BeforeRequestCallable)
T_error_handler = t.TypeVar("T_error_handler", bound=ErrorHandlerCallable)
T_template_filter = t.TypeVar("T_template_filter", bound=TemplateFilterCallable)
T_template_global = t.TypeVar("T_template_global", bound=TemplateGlobalCallable)
T_template_test = t.TypeVar("T_template_test", bound=TemplateTestCallable)
T_teardown = t.TypeVar("T_teardown", bound=TeardownCallable)
T_url_defaults = t.TypeVar("T_url_defaults", bound=URLDefaultCallable)
T_url_value_preprocessor = t.TypeVar(
    "T_url_value_preprocessor", bound=URLValuePreprocessorCallable
)


def setupmethod(f: F) -> F:
    """Wraps a method so that it performs a setup check if requested.

    The method is automatically run the first time the application context
    is pushed.
    """

    def wrapper_func(self, *args: t.Any, **kwargs: t.Any) -> t.Any:
        if self._is_setup_finished():
            raise AssertionError(
                "A setup function was called after the first request was handled."
                " This usually indicates a bug in the application where a method"
                " was called too late. To fix this make sure to call all setup"
                " methods before the first request."
            )
        return f(self, *args, **kwargs)

    return t.cast(F, wrapper_func)


class Flask:
    """The flask object implements a WSGI application and acts as the central
    object.  It is passed the name of the module or package of the
    application.  Once it is created it will act as a central registry for
    the view functions, the URL rules, template configuration and much more.

    The name of the package is used to resolve resources from inside the
    package or the folder the module is contained in depending on if the
    package parameter resolves to an actual python package (a folder with
    an :file:`__init__.py` file inside) or a standard module (just a single
    file).

    .. versionchanged:: 3.0
        The ``static_folder`` parameter defaults to ``None``.

    .. versionchanged:: 3.0
        The ``instance_relative_config`` parameter defaults to ``True``.

    .. versionchanged:: 3.0
        The ``root_path`` parameter defaults to ``None``.

    .. versionchanged:: 3.0
        The ``template_folder`` parameter defaults to ``None``.

    .. versionchanged:: 2.2
        Added the ``static_url_path`` parameter.

    .. versionchanged:: 2.2
        Added the ``import_name`` parameter.

    .. versionchanged:: 2.0
        Added the ``static_host`` parameter.

    .. versionchanged:: 0.11
        Added the ``root_path`` parameter.

    .. versionchanged:: 0.8
        Added the ``instance_relative_config`` parameter.
    """

    def __init__(
        self,
        import_name: str,
        static_url_path: str | None = None,
        static_folder: str | None = None,
        static_host: str | None = None,
        host_matching: bool = False,
        subdomain_matching: bool = False,
        template_folder: str | None = None,
        instance_path: str | None = None,
        instance_relative_config: bool = True,
        root_path: str | None = None,
    ) -> None:
        self.import_name = import_name
        self.static_url_path = static_url_path
        self.static_folder = static_folder
        self.static_host = static_host
        self.host_matching = host_matching
        self.subdomain_matching = subdomain_matching
        self.template_folder = template_folder
        self.instance_path = instance_path
        self.instance_relative_config = instance_relative_config
        self.root_path = root_path

        # Initialize the Flask app
        self.config = Config(self)
        self.debug: bool | None = None
        self.testing: bool = False
        self.propagate_exceptions: bool | None = None
        self.preserve_context_on_exception: bool | None = None
        self.trap_http_exceptions: bool = False
        self.trap_bad_request_errors: bool = False
        self.secret_key: str | None = None
        self.session_cookie_name: str = "session"
        self.session_cookie_domain: str | None = None
        self.session_cookie_path: str | None = None
        self.session_cookie_httponly: bool = True
        self.session_cookie_secure: bool = False
        self.session_cookie_samesite: str = "Lax"
        self.permanent_session_lifetime: timedelta = timedelta(days=31)
        self.send_file_max_age_default: timedelta = timedelta(hours=12)
        self.use_x_sendfile: bool = False
        self.json_encoder: t.Type[JSONTag] | None = None
        self.json_decoder: t.Type[JSONTag] | None = None
        self.json_sort_keys: bool = True
        self.jsonify_mimetype: str = "application/json"
        self.json_provider_class: t.Type[DefaultJSONProvider] = DefaultJSONProvider
        self.template_context_processors: dict[AppOrBlueprintKey, list[t.Callable[[], dict[str, t.Any]]]] = {}
        self.url_default_functions: dict[AppOrBlueprintKey, list[URLDefaultCallable]] = {}
        self.url_value_preprocessors: dict[AppOrBlueprintKey, list[URLValuePreprocessorCallable]] = {}
        self.url_map_class: type["Rule"] = None  # type: ignore[assignment]
        self.url_map: "Rule" = None  # type: ignore[assignment]
        self.view_functions: dict[str, t.Callable] = {}
        self.error_handlers: dict[int | type[Exception], ErrorHandlerCallable] = {}
        self.before_request_funcs: dict[AppOrBlueprintKey, list[BeforeRequestCallable]] = {}
        self.before_first_request_funcs: list[t.Callable[[], t.Any]] = []
        self.after_request_funcs: dict[AppOrBlueprintKey, list[AfterRequestCallable]] = {}
        self.teardown_request_funcs: dict[AppOrBlueprintKey, list[TeardownCallable]] = {}
        self.teardown_appcontext_funcs: list[TeardownCallable] = []
        self.shell_context_processors: list[t.Callable[[], dict[str, t.Any]]] = []
        self.blueprints: dict[str, "Blueprint"] = {}
        self.extensions: dict[str, t.Any] = {}
        self.url_map_converters: dict[str, type] = {}
        self.cli = cli.FlaskGroup()

        # Set up the application context
        self._got_first_request: bool = False
        self._before_request_lock: t.Lock = t.Lock()

        # Set up the template and static files
        self.jinja_env: Environment = None  # type: ignore[assignment]
        self.jinja_options: dict[str, t.Any] = {}

        # Set up the session interface
        self.session_interface: SessionInterface = SessionInterface()

        # Set up the testing client
        self.test_client_class: type[FlaskClient] | None = None
        self.test_cli_runner_class: type[FlaskCliRunner] | None = None

        # Set up the logging
        self.logger = None

        # Set up the application
        self._setup_finished: bool = False

        # Call the setup method
        self._setup()

    def _setup(self) -> None:
        """Set up the application."""
        self._setup_finished = True

    def _is_setup_finished(self) -> bool:
        """Check if the setup is finished."""
        return self._setup_finished

    def run(
        self,
        host: str | None = None,
        port: int | None = None,
        debug: bool | None = None,
        load_dotenv: bool = True,
        **options: t.Any,
    ) -> None:
        """Runs the application on a local development server.

        Do not use ``run()`` in a production setting. It is not intended to
        meet security and performance requirements for a production server.
        Instead, see :doc:`/deploying` for WSGI server recommendations.

        If the ``debug`` flag is set the server will automatically reload
        for code changes and show a debugger in case an exception happens.

        .. versionchanged:: 2.0
            Added ``load_dotenv`` parameter.

        .. versionchanged:: 0.10
            If port is not specified, 5000 is used.

        .. versionchanged:: 0.9
            Added ``host`` and ``port`` parameters.
        """
        from werkzeug.serving import run_simple

        if host is None:
            host = "127.0.0.1"

        if port is None:
            server_name = self.config.get("SERVER_NAME")
            if server_name and ":" in server_name:
                port = int(server_name.rsplit(":", 1)[1])
            else:
                port = 5000

        if debug is None:
            debug = self.debug

        options.setdefault("use_reloader", debug)
        options.setdefault("use_debugger", debug)
        options.setdefault("threaded", True)

        cli.show_server_banner(self, host, port, debug)

        run_simple(
            host,
            port,
            self,
            **options,
        )

    def test_client(self) -> FlaskClient:
        """Creates a test client for this application.

        For information about unit testing head over to :ref:`testing`.

        .. versionchanged:: 0.7
            The ``test_client`` method now supports the ``with`` block.
        """
        if self.test_client_class is not None:
            return self.test_client_class(self)

        return FlaskClient(self)

    def __call__(
        self, environ: dict[str, t.Any], start_response: t.Callable[..., t.Any]
    ) -> t.Iterable[bytes]:
        """The WSGI application interface."""
        return self.wsgi_app(environ, start_response)

    def wsgi_app(
        self, environ: dict[str, t.Any], start_response: t.Callable[..., t.Any]
    ) -> t.Iterable[bytes]:
        """The actual WSGI application.

        This is not used in production but makes it very easy to test
        without creating a WSGI server.

        .. versionadded:: 0.7
        """
        ctx = self.request_context(environ)
        error: BaseException | None = None
        try:
            try:
                ctx.push()
                response = self.full_dispatch_request()
            except Exception as e:
                error = e
                response = self.handle_exception(e)
            return response(environ, start_response)
        finally:
            if self.should_ignore_error(error):
                error = None
            ctx.auto_pop(error)

    def request_context(self, environ: dict[str, t.Any]) -> RequestContext:
        """Create a :class:`~flask.ctx.RequestContext` representing a
        WSGI environment.

        Use a ``with`` block to push the context, which will make
        :data:`request` point at this request.

        See :doc:`/reqcontext`.

        Typically you'll create a request context in tests::

            with app.request_context(environ):
                assert request.method == 'POST'

        .. versionadded:: 0.7
        """
        return RequestContext(self, environ)

    def app_context(self) -> AppContext:
        """Create an :class:`~flask.ctx.AppContext`.

        Use a ``with`` block to push the context, which will make
        :data:`current_app` point at this application.

        See :doc:`/appcontext`.

        .. versionadded:: 0.9
        """
        return AppContext(self)
