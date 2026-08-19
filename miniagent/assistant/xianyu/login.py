"""CLI-only QR login and atomic Xianyu cookie persistence."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from miniagent.assistant.xianyu.errors import (
    XianyuAuthenticationError,
    XianyuDependencyError,
    XianyuProtocolError,
)
from miniagent.assistant.xianyu.protocol import format_cookie_header

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
_PASSPORT_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_MTOP_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json",
    "Origin": "https://www.goofish.com",
    "Referer": "https://www.goofish.com/",
    "Content-Type": "application/x-www-form-urlencoded",
}
StatusCallback = Callable[[str], None]


def _emit(status: StatusCallback | None, message: str) -> None:
    if status is not None:
        status(message)


async def _generate_tfstk() -> str:
    node = shutil.which("node")
    if node is None:
        raise XianyuDependencyError("闲鱼扫码登录需要 Node.js；消息运行时不需要 Node.js")
    package_resources = files("miniagent.assistant.xianyu.resources")
    script_resource = package_resources.joinpath("gen_tfstk.js")
    et_resource = package_resources.joinpath("et_f.js")
    with as_file(script_resource) as script, as_file(et_resource) as et_script:
        script_dir = Path(tempfile.mkdtemp(prefix="miniagent-xianyu-node-"))
        script_path = script_dir / "gen_tfstk.js"
        et_path = script_dir / "et_f.js"
        shutil.copyfile(script, script_path)
        shutil.copyfile(et_script, et_path)
        process = await asyncio.create_subprocess_exec(
            node,
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.communicate()
            shutil.rmtree(script_dir, ignore_errors=True)
            raise XianyuDependencyError("生成闲鱼登录环境令牌超时") from None
    shutil.rmtree(script_dir, ignore_errors=True)
    value = stdout.decode("utf-8", errors="replace").strip()
    if process.returncode or not value:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise XianyuDependencyError(f"生成闲鱼登录环境令牌失败: {detail or process.returncode}")
    return value


def _qr_lines(value: str) -> str:
    try:
        import qrcode
    except ImportError as error:
        raise XianyuDependencyError(
            "闲鱼扫码登录需要 qrcode；安装 miniagent-python[xianyu]"
        ) from error
    qr = qrcode.QRCode(border=1, box_size=1)
    qr.add_data(value)
    qr.make()
    matrix = qr.get_matrix()
    lines: list[str] = []
    for row in range(0, len(matrix), 2):
        line = ""
        for column, top in enumerate(matrix[row]):
            bottom = matrix[row + 1][column] if row + 1 < len(matrix) else False
            line += "█" if top and bottom else "▀" if top else "▄" if bottom else " "
        lines.append(line)
    return "\n".join(lines)


def _nested_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise XianyuProtocolError("闲鱼登录接口返回了非对象 JSON")
    content = payload.get("content")
    data = content.get("data") if isinstance(content, dict) else None
    if not isinstance(data, dict):
        raise XianyuProtocolError("闲鱼登录接口响应缺少 content.data")
    return data


async def _prepare_qr_session(http: httpx.AsyncClient) -> tuple[dict[str, str], str, str, str, str]:
    """Prepare passport cookies and return polling parameters."""
    await http.get("https://log.mmstat.com/eg.js")
    cna = http.cookies.get("cna") or ""
    if cna:
        http.cookies.set("cna", cna, domain=".goofish.com", path="/")
    for api in (
        "mtop.taobao.idlehome.home.webpc.feed",
        "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get",
    ):
        await http.post(
            f"https://h5api.m.goofish.com/h5/{api}/1.0/",
            params={"jsv": "2.7.2", "appKey": "34839810", "t": str(int(time.time() * 1000)),
                    "sign": "", "v": "1.0", "type": "originaljson", "dataType": "json",
                    "timeout": "20000", "api": api, "sessionOption": "AutoLoginOnly",
                    "spm_cnt": "a21ybx.home.0.0"},
            content="data=%7B%7D",
            headers=_MTOP_HEADERS,
        )
    tfstk = await _generate_tfstk()
    http.cookies.set("tfstk", tfstk, domain=".goofish.com", path="/")
    await http.get("https://passport.goofish.com/mini_login.htm", params={
        "lang": "zh_cn", "appName": "xianyu", "appEntrance": "web", "styleType": "vertical",
        "bizParams": "", "notLoadSsoView": "false", "notKeepLogin": "false", "isMobile": "false",
        "qrCodeFirst": "false", "stie": "77",
    }, headers={**_PASSPORT_HEADERS, "Referer": "https://www.goofish.com/"})
    csrf = http.cookies.get("XSRF-TOKEN") or ""
    cookie2 = http.cookies.get("cookie2") or ""
    common = {"appName": "xianyu", "fromSite": "77", "appEntrance": "web", "_csrf_token": csrf,
              "umidToken": "", "hsiz": cookie2,
              "bizParams": f"taobaoBizLoginFrom=web&renderRefer={quote('https://www.goofish.com/')}",
              "mainPage": "false", "isMobile": "false", "lang": "zh_CN", "returnUrl": "",
              "umidTag": "SERVER"}
    response = await http.get("https://passport.goofish.com/newlogin/qrcode/generate.do", params=common,
                              headers={**_PASSPORT_HEADERS, "Referer": "https://passport.goofish.com/mini_login.htm"})
    response.raise_for_status()
    generated = _nested_data(response.json())
    qr_url, qr_t, qr_ck = (str(generated.get(key) or "") for key in ("codeContent", "t", "ck"))
    if not qr_url or not qr_t or not qr_ck:
        raise XianyuProtocolError("闲鱼二维码响应缺少 codeContent/t/ck")
    return common, cna, qr_url, qr_t, qr_ck


async def _poll_login_token(
    http: httpx.AsyncClient, common: dict[str, str], cna: str, qr_url: str, qr_t: str, qr_ck: str,
    *, poll_interval: float, timeout: float, status: StatusCallback | None,
) -> str:
    """Display a QR code and poll until the user confirms it."""
    _emit(status, _qr_lines(qr_url))
    _emit(status, "请使用闲鱼 App 扫码并在手机上确认")
    deadline, last_state = time.monotonic() + timeout, ""
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        query = await http.post("https://passport.goofish.com/newlogin/qrcode/query.do",
                                params={"appName": "xianyu", "fromSite": "77"},
                                data={**common, "t": qr_t, "ck": qr_ck, "navlanguage": "zh-CN",
                                      "navUserAgent": _UA, "navPlatform": "Win32", "isIframe": "true",
                                      "documentReferer": "https://www.goofish.com/", "defaultView": "sms",
                                      "deviceId": cna},
                                headers={**_PASSPORT_HEADERS, "Content-Type": "application/x-www-form-urlencoded",
                                         "Origin": "https://passport.goofish.com",
                                         "Referer": "https://passport.goofish.com/mini_login.htm"})
        query.raise_for_status()
        data = _nested_data(query.json())
        state = str(data.get("qrCodeStatus") or "")
        if state != last_state:
            _emit(status, {"NEW": "等待扫码", "SCANNED": "已扫码，请在手机确认", "CONFIRMED": "已确认"}.get(state, state))
            last_state = state
        if state == "CONFIRMED":
            return str(data.get("token") or data.get("lgToken") or "")
        if state == "EXPIRED":
            raise TimeoutError("闲鱼登录二维码已过期")
    raise TimeoutError("闲鱼扫码登录超时")


async def _finish_login(http: httpx.AsyncClient, cna: str, login_token: str) -> str:
    """Complete the passport exchange and return the filtered cookie header."""
    if login_token:
        completed = await http.post("https://passport.goofish.com/login_token/login.do",
                                    params={"token": login_token, "subFlow": "DIALOG_CHECK_LOGIN_RPC",
                                            "nextCode": "0018", "bizScene": "qrcode", "confirm": "true"},
                                    data={"deviceId": cna},
                                    headers={**_PASSPORT_HEADERS, "Content-Type": "application/x-www-form-urlencoded",
                                             "Origin": "https://passport.goofish.com",
                                             "Referer": "https://passport.goofish.com/mini_login.htm"})
        completed.raise_for_status()
    if not http.cookies.get("unb"):
        raise TimeoutError("闲鱼扫码登录超时或未返回登录 Cookie")
    await http.post("https://h5api.m.goofish.com/h5/mtop.idle.web.user.page.nav/1.0/",
                    params={"jsv": "2.7.2", "appKey": "34839810", "t": str(int(time.time() * 1000)),
                            "sign": "", "v": "1.0", "type": "originaljson", "dataType": "json",
                            "timeout": "20000", "api": "mtop.idle.web.user.page.nav",
                            "sessionOption": "AutoLoginOnly"}, content="data=%7B%7D", headers=_MTOP_HEADERS)
    cookies = {cookie.name: cookie.value for cookie in http.cookies.jar
               if cookie.domain and ("goofish.com" in cookie.domain or "mmstat.com" in cookie.domain)}
    if not cookies.get("unb") or not cookies.get("_m_h5_tk"):
        raise XianyuAuthenticationError("扫码成功，但登录 Cookie 不完整")
    return format_cookie_header(cookies)


async def qr_login(
    *,
    poll_interval: float = 3.0,
    timeout: float = 120.0,
    status: StatusCallback | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Complete QR login and return a cookie header without persisting it."""
    async with httpx.AsyncClient(
        headers={"User-Agent": _UA},
        timeout=httpx.Timeout(15),
        follow_redirects=True,
        transport=transport,
    ) as http:
        common, cna, qr_url, qr_t, qr_ck = await _prepare_qr_session(http)
        login_token = await _poll_login_token(
            http, common, cna, qr_url, qr_t, qr_ck,
            poll_interval=poll_interval, timeout=timeout, status=status,
        )
        return await _finish_login(http, cna, login_token)


def persist_cookie(config_path: str | Path, cookie: str) -> bool:
    """Atomically replace secrets.xianyu_cookie; return whether disk changed."""
    path = Path(config_path)
    current: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"config.user.json 不是有效 JSON: {error}") from error
        if not isinstance(loaded, dict):
            raise ValueError("config.user.json 顶层必须是对象")
        current = loaded
    secrets = current.setdefault("secrets", {})
    if not isinstance(secrets, dict):
        raise ValueError("config.user.json 的 secrets 必须是对象")
    if secrets.get("xianyu_cookie") == cookie:
        return False
    secrets["xianyu_cookie"] = cookie
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(current, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


__all__ = ["persist_cookie", "qr_login"]
