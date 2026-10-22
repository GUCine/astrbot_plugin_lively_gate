"""門禁：只放行配置裡的 QQ 群，私聊全部放行；群和私聊可以各用一個模型。

這個插件的配置也是整個套件的公共配置：主人、服務的群、機器人 QQ、名字、平台填在這裡，同套件的其他插件沒單獨填時都來這裡取。

機器人名字（bot_name）留空時不啟用「喊名字當 @」。
"""

import asyncio
import random
import re
import sys
import time

from astrbot.api import AstrBotConfig, logger, sp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.message import TextPart

from .lively_common import PLUGIN_DATA, Settings, id_list

# AstrBot 存會話級模型偏好用的鍵（等同 ProviderManager.set_provider 寫入的鍵）
KEY = "provider_perf_chat_completion"

# 傾聽緩衝：被叫到後先等這個人說完，再把連發的幾條合成一條交給模型
LISTEN_IN_DM = False  # 消息合併暫不啟用
LISTEN_IN_GROUP = False
WAIT_DONE = 1.5  # 以 ？！ 結尾或較長，像是說完了
WAIT_NORMAL = 3.0
WAIT_MORE = 5.0  # 很短或以 ，、… 結尾，明顯還有下文（打下一句大約要這麼久）
DM_WAIT_CUT = 0.5  # 私聊縮短（不作用於 WAIT_MORE）
MAX_LISTEN = 10.0  # 從第一條算起最多等這麼久

# 發送前過濾：模型偶爾把系統提示或 DeepSeek 原生工具呼叫標記寫進正文
REMINDER_RE = re.compile(r"<system_reminder>.*?(?:</system_reminder>|$)", re.DOTALL)
DSML_RE = re.compile(r"<[^<>]{0,8}DSML|DSML")
# 表情包插件（meme_manager）的「&&標籤&&」：模型調工具前單獨吐的那一段不經過它的 on_llm_response，
# 整段只有標籤時它也清不掉，就會原樣發出去。在它之後再補一道：換成那個分類的一張圖，沒有這個分類就刪掉
MEME_TAG_RE = re.compile(r"&&([A-Za-z][\w-]{0,30})&&")
MEME_DIR = PLUGIN_DATA / "meme_manager" / "memes"
MEME_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# 表情包：NapCat 會在圖片上標 sub_type=1 或說明文字「[動畫表情]」之類
STICKER_WITH_TEXT = "（对方配的图是表情包，只代表语气，重点回应文字，不用描述图的画面）"
# 收藏的表情、梗圖在 NapCat 裡是普通圖片，認不出來，所以文字配圖一律交給模型自己判斷
TEXT_WITH_IMAGE = (
    "（对方是文字配图：先回应文字；图如果是表情包或梗图，就当作语气，不用描述；"
    "如果是截图或照片，再结合着看）"
)
STICKER_ONLY = (
    "（对方只发了个表情包，像真人一样简单接一句或回个表情就行，不用描述画面）"
)

# 只 @ 一下、或只喊一聲名字：等這個人把話說完（常見的是先 @ 再單獨發圖）
# AstrBot 自帶的空 @ 等待會把隨後的純圖片吞掉，所以在它之前自己處理
CALL_FIRST_WAIT = 15.0  # 等第一條下文
CALL_MORE_WAIT = 2.0  # 有下文後，再等一會兒看還有沒有
CALL_MAX_WAIT = 25.0
EMPTY_CALL_TEXT = "[叫了你一声]"
EMPTY_CALL_HINT = (
    "（对方只是叫了你一下，什么也没说：像真人一样回个「嗯？」「咋啦」就行，一句话）"
)


# 句子中間喊名字：只認明顯在叫它的說法，在聊它的（「加<名字>好友」「<名字>的尾巴」）不算
def _name_res(name: str) -> tuple[re.Pattern, re.Pattern]:
    n = re.escape(name)
    call = re.compile(
        rf"{n}[呢呀啊吗嘛哦喔～~！!？?。\s]*$"  # 句尾喊：早啊<名字>、<名字>呢
        rf"|(?:^|[，,。！!？?\s]){n}[，,！!？?\s]"  # 單獨一段喊：哈哈 <名字>，……
        rf"|{n}(?:你|您|快|来|帮|给我|说说|评|觉得|怎么看|在吗|在不在|出来)"
        rf"|(?:问问|问下|问一下|让|叫|喊|找|请){n}"
    )
    not_call = re.compile(rf"{n}的|{n}好友|加{n}|私信{n}|私聊{n}")
    return call, not_call


def _group_providers(lines) -> dict[str, str]:
    """「群號=模型ID」一行一個"""
    out = {}
    for line in lines or []:
        gid, _, pid = str(line).replace("：", ":").replace(":", "=").partition("=")
        if gid.strip().isdigit() and pid.strip():
            out[gid.strip()] = pid.strip()
    return out


NAME_CALL_COOLDOWN = 20.0  # 同一個群，這種叫法 20 秒內只認一次，防止刷屏連環觸發

# 被叫去評價時模型愛推脫（尤其聊天記錄裡剛推過一次），在這條消息後面直接提醒
REVIEW_RE = re.compile(
    r"锐评|点评|评价|评一下|评评|怎么看待|如何看待|你觉得.{0,20}(怎么样|如何|咋样)"
)
REVIEW_HINT = (
    "（对方在请你评价：不熟的人或作品先用联网搜索查一下，再明确表态——喜欢、一般、不行还是有意思——"
    "然后说一两个具体理由，可以毒舌；有争议就说大概怎么回事加上你的看法；"
    "别推说没见过、不评、怕传谣、换个话题，之前推掉过也没关系，这次好好评）"
)


def _clean(text: str) -> str:
    text = REMINDER_RE.sub("", text)
    m = DSML_RE.search(text)
    if m:
        text = text[: m.start()]
    return text.rstrip()


def _count_stickers(event: AstrMessageEvent) -> tuple[int, int, list]:
    """返回 (圖片數, 其中表情包數, 每張圖的標記)，讀的是 NapCat 原始消息。"""
    raw = getattr(event.message_obj, "raw_message", None)
    try:
        segs = raw.get("message") if raw is not None else None
    except Exception:
        segs = None
    if not isinstance(segs, list):
        return 0, 0, []
    images = stickers = 0
    marks = []
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        t = seg.get("type")
        data = seg.get("data") or {}
        if t == "mface":
            images += 1
            stickers += 1
            marks.append("mface")
        elif t == "image":
            images += 1
            sub = str(data.get("sub_type", data.get("subType", "")))
            summary = str(data.get("summary", ""))
            marks.append(f"sub_type={sub or '-'} summary={summary or '-'}")
            if sub == "1" or "表情" in summary:
                stickers += 1
    return images, stickers, marks


def _strip_old_images(contexts) -> int:
    """歷史裡的舊圖換成「[圖片]」，只保留上一輪使用者消息裡的圖。返回換掉的張數。

    AstrBot 會把圖片以 base64 存進歷史，之後每次請求都重新發給模型，又大又慢。
    """
    if not isinstance(contexts, list):
        return 0
    last_user = max(
        (
            i
            for i, m in enumerate(contexts)
            if isinstance(m, dict) and m.get("role") == "user"
        ),
        default=-1,
    )
    removed = 0
    for i, m in enumerate(contexts):
        if i == last_user or not isinstance(m, dict):
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        if not any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            continue
        new = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "image_url":
                removed += 1
                new.append({"type": "text", "text": "[图片]"})
            else:
                new.append(p)
        m["content"] = new
    return removed


def _wait_for(text: str, is_dm: bool) -> float:
    t = (text or "").strip()
    if t.endswith(("？", "?", "！", "!")) or len(t) >= 30:
        w = WAIT_DONE
    elif len(t) <= 5 or t.endswith(("，", ",", "、", "…", "...")):
        w = WAIT_MORE
    else:
        w = WAIT_NORMAL
    if is_dm and w < WAIT_MORE:
        w = max(1.0, w - DM_WAIT_CUT)
    return w


@register(
    "astrbot_plugin_lively_gate",
    "GUCine",
    "門禁和公共設定：只放行指定 QQ 群、群聊/私聊分別用不同模型、喊名字當 @、表情包識別、攔截洩漏的內部標記",
    "2.0.0",
)
class LivelyGate(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.cfg = config if config is not None else {}
        self.s = Settings(context, self.cfg)
        # 名字留空：不啟用「喊名字當 @」（空名字會讓正則匹配所有消息）
        self.name = self.s.bot_name().strip()
        self.name_call_re, self.name_not_call_re = (
            _name_res(self.name) if self.name else (None, None)
        )
        self.ignored = set(id_list(self.cfg.get("ignored_senders", [])))
        self._listening: dict[tuple[str, str], dict] = {}
        self._calls: dict[tuple[str, str], dict] = {}
        self._name_call_at: dict[str, float] = {}

    async def _bind(self, umo: str, pid: str):
        cur = await sp.get_async("umo", umo, KEY, None)
        if cur != pid:
            await sp.put_async("umo", umo, KEY, pid)

    def _served(self, event: AstrMessageEvent) -> bool:
        if str(event.get_sender_id() or "").strip() in self.ignored:
            return False
        gid = str(event.get_group_id() or "").strip()
        return not gid or gid in self.s.groups()

    def _is_empty_call(self, event: AstrMessageEvent) -> bool:
        comps = [
            c
            for c in event.get_messages()
            if not (isinstance(c, Plain) and not c.text.strip())
        ]
        if len(comps) != 1:
            return False
        c = comps[0]
        if isinstance(c, At):
            return str(c.qq) == str(event.get_self_id())
        if isinstance(c, Plain):
            prefixes = self.context.get_config(umo=event.unified_msg_origin).get(
                "wake_prefix", []
            )
            return c.text.strip() in [p for p in prefixes if p and p != "/"]
        return False

    # 優先順序比 AstrBot 自帶的會話控制、空 @ 等待都高
    @filter.event_message_type(filter.EventMessageType.ALL, priority=sys.maxsize + 1)
    async def empty_call(self, event: AstrMessageEvent):
        if not self._served(event):
            return
        key = (event.unified_msg_origin, str(event.get_sender_id() or ""))
        state = self._calls.get(key)
        if state is not None:
            # 叫完之後發的下文：並進那一條，本條不單獨處理
            text = (event.message_str or "").strip()
            if text:
                state["texts"].append(text)
            state["media"].extend(
                c for c in event.get_messages() if not isinstance(c, (At, Plain))
            )
            state["last"] = time.monotonic()
            event.stop_event()
            return
        if not self._is_empty_call(event):
            return
        now = time.monotonic()
        state = {"texts": [], "media": [], "start": now, "last": None}
        self._calls[key] = state
        try:
            while True:
                if state["last"] is None:
                    end = state["start"] + CALL_FIRST_WAIT
                else:
                    end = min(
                        state["last"] + CALL_MORE_WAIT, state["start"] + CALL_MAX_WAIT
                    )
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.5))
        finally:
            self._calls.pop(key, None)
        text = "\n".join(state["texts"])
        if text or state["media"]:
            event.message_str = text
            logger.info(
                f"[lively_gate] 叫了一聲後接著說了 {len(state['texts'])} 段文字、"
                f"{len(state['media'])} 個圖片等: {event.unified_msg_origin}"
            )
        else:
            event.message_str = EMPTY_CALL_TEXT
            event.set_extra("lively_empty_call", True)
            logger.info(
                f"[lively_gate] 只叫了一聲，沒有下文: {event.unified_msg_origin}"
            )
        # 多加一段文字：既把內容帶上，也讓自帶的空 @ 等待不再接管這條
        event.message_obj.message.append(Plain(event.message_str))
        event.message_obj.message.extend(state["media"])

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def gate(self, event: AstrMessageEvent):
        sender = str(event.get_sender_id() or "").strip()
        if sender in self.ignored:
            event.stop_event()
            return
        self.s.remember_self(event)
        gid = str(event.get_group_id() or "").strip()
        if not gid:
            pid = self.s.provider_id("dm_provider_id")
        elif gid in self.s.groups():
            pid = _group_providers(self.cfg.get("group_providers", [])).get(gid, "")
            if not pid or self.context.get_provider_by_id(pid) is None:
                pid = self.s.provider_id(
                    "group_provider_id"
                )  # 這個群的密鑰還沒配好時先用預設的
        else:
            event.stop_event()
            return
        # 沒選模型就不綁定，交給 AstrBot 預設的
        if pid and self.context.get_provider_by_id(pid) is not None:
            await self._bind(event.unified_msg_origin, pid)
        is_dm = not gid
        if (
            not is_dm
            and not event.is_at_or_wake_command
            and self._called_by_name(event)
        ):
            now = time.monotonic()
            if now - self._name_call_at.get(gid, 0) >= NAME_CALL_COOLDOWN:
                self._name_call_at[gid] = now
                event.is_at_or_wake_command = True
                logger.info(
                    f"[lively_gate] 句子裡叫了{self.name}，當作 @: {event.unified_msg_origin}"
                )
        if (LISTEN_IN_DM if is_dm else LISTEN_IN_GROUP) and sender:
            await self._listen(event, sender, is_dm)
        self._mark_stickers(event)

    def _called_by_name(self, event: AstrMessageEvent) -> bool:
        if not self.name or self.name_call_re is None:
            return False
        text = (event.message_str or "").strip()
        if self.name not in text or self.name_not_call_re.search(text):
            return False
        # @ 了別人，是在跟別人聊它
        self_id = str(event.get_self_id())
        if any(
            isinstance(c, At) and str(c.qq) != self_id for c in event.get_messages()
        ):
            return False
        return bool(self.name_call_re.search(text))

    def _mark_stickers(self, event: AstrMessageEvent):
        if not any(isinstance(c, Image) for c in event.get_messages()):
            return
        images, stickers, marks = _count_stickers(event)
        has_text = bool((event.message_str or "").strip())
        logger.info(
            f"[lively_gate] 圖片 {images} 張，表情包 {stickers} 張，帶文字 {has_text}: {marks}"
        )
        if has_text:
            all_stickers = images > 0 and stickers == images
            event.set_extra(
                "lively_sticker_hint",
                STICKER_WITH_TEXT if all_stickers else TEXT_WITH_IMAGE,
            )
        elif stickers and stickers == images:
            event.set_extra("lively_sticker_hint", STICKER_ONLY)

    @filter.on_llm_request(priority=5)
    async def sticker_hint(self, event: AstrMessageEvent, req):
        try:
            n = _strip_old_images(getattr(req, "contexts", None))
            if n:
                logger.info(
                    f"[lively_gate] 歷史裡 {n} 張舊圖換成了文字: {event.unified_msg_origin}"
                )
        except Exception as e:
            logger.warning(f"[lively_gate] 清理歷史圖片失敗: {e}")
        if event.get_extra("lively_empty_call"):
            req.extra_user_content_parts.append(
                TextPart(text=EMPTY_CALL_HINT).mark_as_temp()
            )
        hint = event.get_extra("lively_sticker_hint")
        if hint:
            req.extra_user_content_parts.append(TextPart(text=hint).mark_as_temp())
        if REVIEW_RE.search(event.message_str or ""):
            req.extra_user_content_parts.append(
                TextPart(text=REVIEW_HINT).mark_as_temp()
            )
            logger.info(
                f"[lively_gate] 評價請求，已提醒好好評: {event.unified_msg_origin}"
            )

    async def _listen(self, event: AstrMessageEvent, sender: str, is_dm: bool):
        key = (event.unified_msg_origin, sender)
        state = self._listening.get(key)
        if state is not None:
            # 正在傾聽這個人：併入第一條，本條不再單獨處理
            state["texts"].append(event.message_str or "")
            state["images"].extend(
                c for c in event.get_messages() if isinstance(c, Image)
            )
            state["last"] = time.monotonic()
            state["wait"] = _wait_for(event.message_str, is_dm)
            event.stop_event()
            return
        if not event.is_at_or_wake_command:
            return
        now = time.monotonic()
        state = {
            "texts": [],
            "images": [],
            "start": now,
            "last": now,
            "wait": _wait_for(event.message_str, is_dm),
        }
        self._listening[key] = state
        try:
            while True:
                end = min(state["last"] + state["wait"], state["start"] + MAX_LISTEN)
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)
        finally:
            self._listening.pop(key, None)
        extra = [t.strip() for t in state["texts"] if t and t.strip()]
        if extra or state["images"]:
            event.message_str = "\n".join(
                [t for t in [(event.message_str or "").strip(), *extra] if t]
            )
            event.message_obj.message.extend(state["images"])
            logger.info(
                f"[lively_gate] 傾聽合併 {len(state['texts']) + 1} 條，"
                f"等待 {time.monotonic() - state['start']:.1f}s: {event.unified_msg_origin}"
            )

    @filter.on_decorating_result(priority=100000)
    async def strip_leak(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not isinstance(result.chain, list):
            return
        changed = False
        chain = []
        for comp in result.chain:
            if isinstance(comp, Plain) and (
                "system_reminder" in comp.text or "DSML" in comp.text
            ):
                comp = Plain(_clean(comp.text))
                changed = True
                if not comp.text.strip():
                    continue
            chain.append(comp)
        if not changed:
            return
        logger.warning(
            f"[lively_gate] 已攔截回覆中的內部內容: {event.unified_msg_origin}"
        )
        if not any(not isinstance(c, Plain) or c.text.strip() for c in chain):
            event.stop_event()
            return
        result.chain = chain

    # 排在表情包插件（99999）之後：它處理完還剩下的標籤才歸這裡
    @filter.on_decorating_result(priority=99990)
    async def meme_leftover(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result or not isinstance(result.chain, list):
            return
        if not any(isinstance(c, Plain) and "&&" in c.text for c in result.chain):
            return
        chain, tags = [], []
        for comp in result.chain:
            if isinstance(comp, Plain) and MEME_TAG_RE.search(comp.text):
                tags += MEME_TAG_RE.findall(comp.text)
                text = MEME_TAG_RE.sub("", comp.text).strip()
                if text:
                    chain.append(Plain(text))
                continue
            chain.append(comp)
        if not tags:
            return
        img = self._meme_image(tags[0])
        if img:
            chain.append(Image.fromFileSystem(str(img)))
        logger.info(
            f"[lively_gate] 回覆裡剩下沒處理的表情標籤 {tags}，"
            f"{'换成了表情图' if img else '没有这个分类，删掉了'}: {event.unified_msg_origin}"
        )
        if not chain:
            event.stop_event()
            return
        result.chain = chain

    @staticmethod
    def _meme_image(tag: str):
        d = MEME_DIR / tag
        try:
            files = (
                [f for f in d.iterdir() if f.suffix.lower() in MEME_EXTS]
                if d.is_dir()
                else []
            )
        except Exception:
            files = []
        return random.choice(files) if files else None
