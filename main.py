"""Main module of kytos/telemetry Network Application.

Napp to deploy In-band Network Telemetry over Ethernet Virtual Circuits

"""

import asyncio
import pathlib
from datetime import datetime

import napps.kytos.telemetry_int.kytos_api_helper as api
from napps.kytos.telemetry_int import settings, utils
from tenacity import RetryError

from kytos.core import KytosEvent, KytosNApp, log, rest
from kytos.core.helpers import alisten_to, avalidate_openapi_request, load_spec
from kytos.core.rest_api import HTTPException, JSONResponse, Request, aget_json_or_400

from .exceptions import (
    EVCHasINT,
    EVCHasNoINT,
    EVCNotFound,
    FlowsNotFound,
    ProxyPortNotFound,
    ProxyPortSameSourceIntraEVC,
    ProxyPortStatusNotUP,
    UnrecoverableError,
)
from .managers.int import INTManager

# pylint: disable=fixme


class Main(KytosNApp):
    """Main class of kytos/telemetry NApp.

    This class is the entry point for this NApp.
    """

    spec = load_spec(pathlib.Path(__file__).parent / "openapi.yml")

    def setup(self):
        """Replace the '__init__' method for the KytosNApp subclass.

        The setup method is automatically called by the controller when your
        application is loaded.

        So, if you have any setup routine, insert it here.
        """

        self.int_manager = INTManager(self.controller)
        self._ofpt_error_lock = asyncio.Lock()

    def execute(self):
        """Run after the setup method execution.

        You can also use this method in loop mode if you add to the above setup
        method a line like the following example:

            self.execute_as_loop(30)  # 30-second interval.
        """

    def shutdown(self):
        """Run when your NApp is unloaded.

        If you have some cleanup procedure, insert it here.
        """

    @rest("v1/evc/enable", methods=["POST"])
    async def enable_telemetry(self, request: Request) -> JSONResponse:
        """REST to enable INT flows on EVCs.

        If a list of evc_ids is empty, it'll enable on non-INT EVCs.
        """
        await avalidate_openapi_request(self.spec, request)

        try:
            content = await aget_json_or_400(request)
            evc_ids = content["evc_ids"]
            force = content.get("force", False)
            if not isinstance(force, bool):
                raise TypeError(f"'force' wrong type: {type(force)} expected bool")
        except (TypeError, KeyError):
            raise HTTPException(400, detail=f"Invalid payload: {content}")

        try:
            evcs = (
                await api.get_evcs()
                if len(evc_ids) != 1
                else await api.get_evc(evc_ids[0])
            )
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)

        if evc_ids:
            evcs = {evc_id: evcs.get(evc_id, {}) for evc_id in evc_ids}
        else:
            evcs = {k: v for k, v in evcs.items() if not utils.has_int_enabled(v)}
            if not evcs:
                # There's no non-INT EVCs to get enabled.
                return JSONResponse(list(evcs.keys()))

        try:
            # First, it tries to get and remove the existing INT flows like mef_eline
            stored_flows = await api.get_stored_flows(
                [
                    utils.get_cookie(evc_id, settings.INT_COOKIE_PREFIX)
                    for evc_id in evcs
                ]
            )
            await self.int_manager._remove_int_flows(stored_flows)
            await self.int_manager.enable_int(evcs, force)
        except (EVCNotFound, FlowsNotFound, ProxyPortNotFound) as exc:
            raise HTTPException(404, detail=str(exc))
        except (EVCHasINT, ProxyPortStatusNotUP, ProxyPortSameSourceIntraEVC) as exc:
            raise HTTPException(409, detail=str(exc))
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)
        except UnrecoverableError as exc:
            exc_error = str(exc)
            log.error(exc_error)
            raise HTTPException(500, detail=exc_error)

        return JSONResponse(list(evcs.keys()), status_code=201)

    @rest("v1/evc/disable", methods=["POST"])
    async def disable_telemetry(self, request: Request) -> JSONResponse:
        """REST to disable/remove INT flows for an EVC_ID

        If a list of evc_ids is empty, it'll disable on all INT EVCs.
        """
        await avalidate_openapi_request(self.spec, request)

        try:
            content = await aget_json_or_400(request)
            evc_ids = content["evc_ids"]
            force = content.get("force", False)
            if not isinstance(force, bool):
                raise TypeError(f"'force' wrong type: {type(force)} expected bool")
        except (TypeError, KeyError):
            raise HTTPException(400, detail=f"Invalid payload: {content}")

        try:
            evcs = (
                await api.get_evcs()
                if len(evc_ids) != 1
                else await api.get_evc(evc_ids[0])
            )
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)

        if evc_ids:
            evcs = {evc_id: evcs.get(evc_id, {}) for evc_id in evc_ids}
        else:
            evcs = {k: v for k, v in evcs.items() if utils.has_int_enabled(v)}
            if not evcs:
                # There's no INT EVCs to get disabled.
                return JSONResponse(list(evcs.keys()))

        try:
            await self.int_manager.disable_int(evcs, force)
        except EVCNotFound as exc:
            raise HTTPException(404, detail=str(exc))
        except EVCHasNoINT as exc:
            raise HTTPException(409, detail=str(exc))
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)
        except UnrecoverableError as exc:
            exc_error = str(exc)
            log.error(exc_error)
            raise HTTPException(500, detail=exc_error)

        return JSONResponse(list(evcs.keys()))

    @rest("v1/evc")
    async def get_evcs(self, _request: Request) -> JSONResponse:
        """REST to return the list of EVCs with INT enabled"""
        try:
            evcs = await api.get_evcs(**{"metadata.telemetry.enabled": "true"})
            return JSONResponse(evcs)
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)
        except UnrecoverableError as exc:
            exc_error = str(exc)
            log.error(exc_error)
            raise HTTPException(500, detail=exc_error)

    @rest("v1/evc/redeploy", methods=["PATCH"])
    async def redeploy_telemetry(self, request: Request) -> JSONResponse:
        """REST to redeploy INT on EVCs.

        If a list of evc_ids is empty, it'll redeploy on all INT EVCs.
        """
        await avalidate_openapi_request(self.spec, request)

        try:
            content = await aget_json_or_400(request)
            evc_ids = content["evc_ids"]
        except (TypeError, KeyError):
            raise HTTPException(400, detail=f"Invalid payload: {content}")

        try:
            evcs = (
                await api.get_evcs()
                if len(evc_ids) != 1
                else await api.get_evc(evc_ids[0])
            )
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)

        if evc_ids:
            evcs = {evc_id: evcs.get(evc_id, {}) for evc_id in evc_ids}
        else:
            evcs = {k: v for k, v in evcs.items() if utils.has_int_enabled(v)}
            if not evcs:
                raise HTTPException(404, detail="There aren't INT EVCs to redeploy")

        try:
            await self.int_manager.redeploy_int(evcs)
        except (EVCNotFound, FlowsNotFound, ProxyPortNotFound) as exc:
            raise HTTPException(404, detail=str(exc))
        except ProxyPortSameSourceIntraEVC as exc:
            raise HTTPException(409, detail=str(exc))
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)
        except UnrecoverableError as exc:
            exc_error = str(exc)
            log.error(exc_error)
            raise HTTPException(500, detail=exc_error)

        return JSONResponse(list(evcs.keys()), status_code=201)

    @rest("v1/evc/compare")
    async def evc_compare(self, _request: Request) -> JSONResponse:
        """List and compare which INT EVCs have flows installed comparing with
        mef_eline flows and telemetry metadata. You should use this endpoint
        to confirm if both the telemetry metadata is still coherent and also
        the minimum expected number of flows. A list of EVCs will get returned
        with the inconsistent INT EVCs. If you encounter any inconsistent
        EVC you need to analyze the situation and then decide if you'd
        like to force enable or disable INT.
        """

        try:
            int_flows, mef_flows, evcs = await asyncio.gather(
                api.get_stored_flows(
                    [
                        (
                            settings.INT_COOKIE_PREFIX << 56,
                            settings.INT_COOKIE_PREFIX << 56 | 0xFFFFFFFFFFFFFF,
                        ),
                    ]
                ),
                api.get_stored_flows(
                    [
                        (
                            settings.MEF_COOKIE_PREFIX << 56,
                            settings.MEF_COOKIE_PREFIX << 56 | 0xFFFFFFFFFFFFFF,
                        ),
                    ]
                ),
                api.get_evcs(),
            )
        except RetryError as exc:
            exc_error = str(exc.last_attempt.exception())
            log.error(exc_error)
            raise HTTPException(503, detail=exc_error)
        except UnrecoverableError as exc:
            exc_error = str(exc)
            log.error(exc_error)
            raise HTTPException(500, detail=exc_error)

        response = [
            {"id": k, "name": evcs[k]["name"], "compare_reason": v}
            for k, v in self.int_manager.evc_compare(int_flows, mef_flows, evcs).items()
        ]
        return JSONResponse(response)

    @alisten_to("kytos/mef_eline.evcs_loaded")
    async def on_mef_eline_evcs_loaded(self, event: KytosEvent) -> None:
        """Handle kytos/mef_eline.evcs_loaded."""
        self.int_manager.load_uni_src_proxy_ports(event.content)

    @alisten_to("kytos/of_multi_table.enable_table")
    async def on_table_enabled(self, event):
        """Handle of_multi_table.enable_table."""
        table_group = event.content.get("telemetry_int", {})
        if not table_group:
            return
        for group in table_group:
            if group not in settings.TABLE_GROUP_ALLOWED:
                log.error(
                    f'The table group "{group}" is not allowed for '
                    f"telemetry_int. Allowed table groups are "
                    f"{settings.TABLE_GROUP_ALLOWED}"
                )
                return
        self.int_manager.flow_builder.table_group.update(table_group)
        content = {"group_table": self.int_manager.flow_builder.table_group}
        event_out = KytosEvent(name="kytos/telemetry_int.enable_table", content=content)
        await self.controller.buffers.app.aput(event_out)

    @alisten_to("kytos/mef_eline.deleted")
    async def on_evc_deleted(self, event: KytosEvent) -> None:
        """On EVC deleted."""
        content = event.content
        if (
            "metadata" in content
            and "telemetry" in content["metadata"]
            and content["metadata"]["telemetry"]["enabled"]
        ):
            evc_id = content["evc_id"]
            log.info(f"Event mef_eline.deleted on EVC id: {evc_id}")
            await self.int_manager.disable_int({evc_id: content}, force=True)

    @alisten_to("kytos/mef_eline.undeployed")
    async def on_evc_undeployed(self, event: KytosEvent) -> None:
        """On EVC undeployed."""
        content = event.content
        if (
            not content["enabled"]
            and "metadata" in content
            and "telemetry" in content["metadata"]
            and content["metadata"]["telemetry"]["enabled"]
        ):
            metadata = {
                "telemetry": {
                    "enabled": True,
                    "status": "DOWN",
                    "status_reason": ["undeployed"],
                    "status_updated_at": datetime.utcnow().strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                }
            }
            evcs = {content["evc_id"]: content}
            await self.int_manager.remove_int_flows(evcs, metadata, force=True)

    @alisten_to("kytos/topology.link_down")
    async def on_link_down(self, event):
        """Handle topology.link_down."""
        await self.int_manager.handle_pp_link_down(event.content["link"])

    @alisten_to("kytos/topology.link_up")
    async def on_link_up(self, event):
        """Handle topology.link_up."""
        await self.int_manager.handle_pp_link_up(event.content["link"])

    @alisten_to("kytos/flow_manager.flow.error")
    async def on_flow_mod_error(self, event: KytosEvent):
        """On flow mod errors.

        Only OFPT_ERRORs will be handled, telemetry_int already uses force: true
        """
        flow = event.content["flow"]
        if any(
            (
                event.content.get("error_exception"),
                event.content.get("error_command") != "add",
                flow.cookie >> 56 != settings.INT_COOKIE_PREFIX,
            )
        ):
            return

        async with self._ofpt_error_lock:
            evc_id = utils.get_id_from_cookie(flow.cookie)
            evc = await api.get_evc(evc_id, exclude_archived=False)
            if (
                not evc
                or "telemetry" not in evc[evc_id]["metadata"]
                or "enabled" not in evc[evc_id]["metadata"]["telemetry"]
                or not evc[evc_id]["metadata"]["telemetry"]["enabled"]
            ):
                return

            metadata = {
                "telemetry": {
                    "enabled": False,
                    "status": "DOWN",
                    "status_reason": ["ofpt_error"],
                    "status_updated_at": datetime.utcnow().strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                }
            }
            log.error(
                f"Disabling EVC({evc_id}) due to OFPT_ERROR, "
                f"error_type: {event.content.get('error_type')}, "
                f"error_code: {event.content.get('error_code')}, "
                f"flow: {flow.as_dict()} "
            )

            evcs = {evc_id: {evc_id: evc_id}}
            await self.int_manager.remove_int_flows(evcs, metadata, force=True)

    @alisten_to("kytos/topology.interfaces.metadata.removed")
    async def on_intf_metadata_removed(self, event: KytosEvent) -> None:
        """On interface metadata removed."""
        await self.int_manager.handle_pp_metadata_removed(event.content["interface"])

    @alisten_to("kytos/topology.interfaces.metadata.added")
    async def on_intf_metadata_added(self, event: KytosEvent) -> None:
        """On interface metadata added."""
        await self.int_manager.handle_pp_metadata_added(event.content["interface"])

    # Event-driven methods: future
    def listen_for_new_evcs(self):
        """Change newly created EVC to INT-enabled EVC based on the metadata field
        (future)"""
        pass

    def listen_for_evc_change(self):
        """Change newly created EVC to INT-enabled EVC based on the
        metadata field (future)"""
        pass

    def listen_for_path_changes(self):
        """Change EVC's new path to INT-enabled EVC based on the metadata field
        when there is a path change. (future)"""
        pass

    def listen_for_topology_changes(self):
        """If the topology changes, make sure it is not the loop ports.
        If so, update proxy ports"""
        pass
