"""Async single-account client for the HTTP portion of the Xianyu web API."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import httpx

from miniagent.assistant.xianyu.errors import XianyuAuthenticationError, XianyuProtocolError
from miniagent.assistant.xianyu.protocol import APP_KEY, IM_APP_KEY, generate_device_id, mtop_sign

_BASE = "https://h5api.m.goofish.com/h5"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json",
    "Origin": "https://www.goofish.com",
    "Referer": "https://www.goofish.com/",
    "Content-Type": "application/x-www-form-urlencoded",
}
DeliveryMode = Literal["free_shipping", "distance_based", "fixed", "pickup_only"]


def _cents(value: str | int | float | Decimal) -> str:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"invalid money value: {value!r}") from error
    if amount < 0:
        raise ValueError("money value must not be negative")
    return str(int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def _validate_publish_args(description: str, image_paths: list[str], delivery: DeliveryMode, shipping_fee: Any) -> None:
    if not description.strip():
        raise ValueError("description must not be empty")
    if not image_paths:
        raise ValueError("at least one image is required")
    if delivery not in {"free_shipping", "distance_based", "fixed", "pickup_only"}:
        raise ValueError(f"unsupported delivery mode: {delivery}")
    if delivery == "fixed" and shipping_fee is None:
        raise ValueError("shipping_fee is required for fixed delivery")


def _post_fee(delivery: DeliveryMode, shipping_fee: Any) -> dict[str, Any]:
    fee: dict[str, Any] = {
        "canFreeShipping": delivery == "free_shipping",
        "supportFreight": delivery != "pickup_only",
        "onlyTakeSelf": delivery == "pickup_only",
    }
    if delivery == "distance_based":
        fee["templateId"] = "-100"
    elif delivery == "fixed":
        fee.update(templateId="0", postPriceInCent=_cents(shipping_fee))
    elif delivery == "pickup_only":
        fee["templateId"] = "0"
    return fee


def _price(current_price: Any, original_price: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if current_price is not None:
        result["priceInCent"] = _cents(current_price)
    if original_price is not None:
        result["origPriceInCent"] = _cents(original_price)
    return result


def _selected_labels(category_data: dict[str, Any]) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for card in category_data.get("cardList") or []:
        card_data = card.get("cardData") or {}
        selected = next(
            (value for value in card_data.get("valuesList") or [] if value.get("isClicked")),
            None,
        )
        if selected:
            labels.append({
                "channelCateName": selected.get("catName"),
                "channelCateId": selected.get("channelCatId"),
                "tbCatId": selected.get("tbCatId"),
                "propertyName": card_data.get("propertyName"),
                "propertyId": card_data.get("propertyId"),
                "isUserClick": "1",
                "from": "newPublishChoice",
                "labelFrom": "newPublish",
                "text": selected.get("catName"),
                "properties": (
                    f"{card_data.get('propertyId')}##{card_data.get('propertyName')}:"
                    f"{selected.get('channelCatId')}##{selected.get('catName')}"
                ),
            })
    return labels


def _image_list(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
            "isQrCode": False,
            "url": image["url"],
            "heightSize": image["height"],
            "widthSize": image["width"],
            "major": index == 0,
            "type": 0,
            "status": "done",
        }
        for index, image in enumerate(images)
    ]


def _publish_body(
    *, description: str, images: list[dict[str, Any]], category_data: dict[str, Any],
    location: dict[str, Any], delivery: DeliveryMode, shipping_fee: Any,
    current_price: Any, original_price: Any, self_pickup: bool,
) -> dict[str, Any]:
    prediction = category_data["categoryPredictResult"]
    return {
        "freebies": False, "itemTypeStr": "b", "quantity": "1", "simpleItem": "true",
        "imageInfoDOList": _image_list(images),
        "itemTextDTO": {"desc": description, "title": description, "titleDescSeparate": False},
        "itemLabelExtList": _selected_labels(category_data),
        "itemPriceDTO": _price(current_price, original_price),
        "userRightsProtocols": [{"enable": False, "serviceCode": "SKILL_PLAY_NO_MIND"}],
        "itemPostFeeDTO": _post_fee(delivery, shipping_fee),
        "itemAddrDTO": {
            "area": location.get("area"), "city": location.get("city"),
            "divisionId": location.get("divisionId"), "gps": f"{location.get('longitude')},{location.get('latitude')}",
            "poiId": location.get("poiId"), "poiName": location.get("poi"), "prov": location.get("prov"),
        },
        "defaultPrice": current_price is None and original_price is None,
        "itemCatDTO": {key: str(prediction.get(key)) for key in ("catId", "catName", "channelCatId", "tbCatId")},
        "uniqueCode": str(int(time.time() * 1000)), "sourceId": "pcMainPublish",
        "bizcode": "pcMainPublish", "publishScene": "pcMainPublish",
        "onlyTakeSelf": bool(self_pickup or delivery == "pickup_only"),
    }


class XianyuClient:
    """Own one cookie jar and connection pool for a single Xianyu account."""

    def __init__(
        self,
        cookies: dict[str, str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        owner_id = str(cookies.get("unb") or "").strip()
        if not owner_id:
            raise XianyuAuthenticationError("Xianyu cookie is missing required 'unb'")
        self.owner_id = owner_id
        self.device_id = generate_device_id(owner_id)
        self.http = httpx.AsyncClient(
            cookies=cookies,
            headers=_HEADERS,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            transport=transport,
        )

    def cookie_dict(self) -> dict[str, str]:
        """Return a copy of the current account cookies."""
        return {cookie.name: cookie.value for cookie in self.http.cookies.jar}

    def cookie_header(self) -> str:
        """Return cookies formatted for an HTTP request header."""
        return "; ".join(f"{k}={v}" for k, v in self.cookie_dict().items())

    def _token(self) -> str:
        value = self.http.cookies.get("_m_h5_tk") or ""
        token = value.split("_", 1)[0]
        if not token:
            raise XianyuAuthenticationError("Xianyu cookie is missing _m_h5_tk")
        return token

    @staticmethod
    def _is_token_expired(payload: dict[str, Any]) -> bool:
        ret = payload.get("ret") or []
        return any("令牌过期" in str(item) or "TOKEN_EXPIRED" in str(item) for item in ret)

    @staticmethod
    def _is_auth_failure(payload: dict[str, Any]) -> bool:
        ret = " ".join(str(item) for item in (payload.get("ret") or []))
        markers = ("FAIL_SYS_SESSION_EXPIRED", "令牌过期", "非法请求", "NEED_LOGIN")
        return any(marker in ret for marker in markers)

    async def _mtop(
        self,
        api: str,
        version: str,
        data: dict[str, Any],
        *,
        retry_token: bool = True,
        extra_params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data_value = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time() * 1000))
        params = {
            "jsv": "2.7.2",
            "appKey": APP_KEY,
            "t": timestamp,
            "sign": mtop_sign(timestamp, self._token(), data_value),
            "v": version,
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api,
            "sessionOption": "AutoLoginOnly",
        }
        if extra_params:
            params.update(extra_params)
        response = await self.http.post(
            f"{_BASE}/{api}/{version}/",
            params=params,
            data={"data": data_value},
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise XianyuProtocolError(f"{api} returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise XianyuProtocolError(f"{api} returned a non-object response")
        if self._is_token_expired(payload) and retry_token:
            await self._mtop(
                "mtop.taobao.idlemessage.pc.loginuser.get",
                "1.0",
                {},
                retry_token=False,
            )
            return await self._mtop(
                api, version, data, retry_token=False, extra_params=extra_params
            )
        if self._is_auth_failure(payload):
            raise XianyuAuthenticationError(f"Xianyu authentication failed: {payload.get('ret')}")
        return payload

    async def get_access_token(self) -> str:
        """Fetch and return the current Xianyu access token."""
        payload = await self._mtop(
            "mtop.taobao.idlemessage.pc.login.token",
            "1.0",
            {"appKey": IM_APP_KEY, "deviceId": self.device_id},
        )
        token = str((payload.get("data") or {}).get("accessToken") or "")
        if not token:
            raise XianyuAuthenticationError("Xianyu did not return an IM access token")
        return token

    async def refresh_login(self) -> dict[str, Any]:
        """Refresh login state and update cookies when the server returns changes."""
        return await self._mtop("mtop.taobao.idlemessage.pc.loginuser.get", "1.0", {})

    async def get_item(self, item_id: str) -> dict[str, Any]:
        """Fetch details for one Xianyu item."""
        return await self._mtop("mtop.taobao.idle.pc.detail", "1.0", {"itemId": str(item_id)})

    async def upload_image(self, path: str | Path) -> dict[str, Any]:
        """Upload a local image and return its CDN metadata."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(str(file_path))
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            raise ValueError("Xianyu media upload only accepts images")
        content = await asyncio.to_thread(file_path.read_bytes)
        upload_headers = {key: value for key, value in _HEADERS.items() if key != "Content-Type"}
        response = await self.http.post(
            "https://stream-upload.goofish.com/api/upload.api",
            params={"floderId": "0", "appkey": "xy_chat", "_input_charset": "utf-8"},
            headers=upload_headers,
            files={"file": (file_path.name, content, mime)},
        )
        response.raise_for_status()
        payload = response.json()
        image = payload.get("object") if isinstance(payload, dict) else None
        if not isinstance(image, dict) or not image.get("url"):
            raise XianyuProtocolError("Xianyu image upload returned no image object")
        width, height = 0, 0
        try:
            width, height = (int(part) for part in str(image.get("pix") or "0x0").split("x", 1))
        except (TypeError, ValueError):
            pass
        return {"url": str(image["url"]), "width": width, "height": height}

    async def recommend_publish_category(
        self, description: str, images: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Recommend a publish category from item description and image metadata."""
        image_infos = [
            {
                "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
                "isQrCode": False,
                "url": image["url"],
                "heightSize": image["height"],
                "widthSize": image["width"],
                "major": index == 0,
                "type": 0,
                "status": "done",
            }
            for index, image in enumerate(images)
        ]
        return await self._mtop(
            "mtop.taobao.idle.kgraph.property.recommend",
            "2.0",
            {
                "title": description,
                "lockCpv": False,
                "multiSKU": False,
                "publishScene": "mainPublish",
                "scene": "newPublishChoice",
                "description": description,
                "imageInfos": image_infos,
                "uniqueCode": str(int(time.time() * 1000)),
            },
        )

    async def get_location(self, longitude: Decimal, latitude: Decimal) -> dict[str, Any]:
        """Resolve a publish location from decimal longitude and latitude."""
        payload = await self._mtop(
            "mtop.taobao.idle.local.poi.get",
            "1.0",
            {"longitude": str(longitude), "latitude": str(latitude)},
        )
        addresses = (payload.get("data") or {}).get("commonAddresses") or []
        if not addresses or not isinstance(addresses[0], dict):
            raise XianyuProtocolError("Xianyu returned no publish address for the coordinates")
        return addresses[0]

    async def publish_item(
        self,
        *,
        image_paths: list[str],
        description: str,
        delivery: DeliveryMode,
        longitude: str | float | Decimal,
        latitude: str | float | Decimal,
        current_price: str | float | Decimal | None = None,
        original_price: str | float | Decimal | None = None,
        shipping_fee: str | float | Decimal | None = None,
        self_pickup: bool = False,
    ) -> dict[str, Any]:
        """Publish an item using validated description, delivery, and media data."""
        _validate_publish_args(description, image_paths, delivery, shipping_fee)
        try:
            lon, lat = Decimal(str(longitude)), Decimal(str(latitude))
        except InvalidOperation as error:
            raise ValueError("longitude and latitude must be decimal numbers") from error
        images = [await self.upload_image(path) for path in image_paths]
        category_response = await self.recommend_publish_category(description, images)
        category_data = category_response.get("data") or {}
        prediction = category_data.get("categoryPredictResult") or {}
        if not prediction.get("catId"):
            raise XianyuProtocolError("Xianyu category recommendation returned no category")
        location = await self.get_location(lon, lat)
        body = _publish_body(
            description=description, images=images, category_data=category_data,
            location=location, delivery=delivery, shipping_fee=shipping_fee,
            current_price=current_price, original_price=original_price, self_pickup=self_pickup,
        )
        return await self._mtop("mtop.idle.pc.idleitem.publish", "1.0", body)

    async def close(self) -> None:
        """Close the client's HTTP connection pool."""
        await self.http.aclose()


__all__ = ["DeliveryMode", "XianyuClient"]
