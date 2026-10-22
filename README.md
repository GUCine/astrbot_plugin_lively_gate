# 群組門禁與共用設定（AstrBot 插件）

「擬真群友」（lively）插件套件的一員，也可以單獨使用。

> 只放行指定 QQ 群、群聊/私聊分別用不同模型、喊名字當 @、表情包識別、攔截洩漏的內部標記。

**English summary:** An [AstrBot](https://github.com/AstrBotDevs/AstrBot) plugin for QQ (aiocqhttp / NapCat). It acts as an allow-list gate (only listed QQ groups are served, private chats always pass), lets groups and private chats use different LLM providers (optionally per group), treats calling the bot by name in a sentence as an @-mention, hints the model about stickers, strips leaked internal markers (`<system_reminder>`, DSML tool-call tags) from replies, and cleans up leftover `&&tag&&` sticker markers that the meme_manager plugin missed. Its settings also serve as the shared settings (owner, groups, bot name, persona wording) for the other plugins of the *lively* suite. All settings are empty by default. License: MIT.

> ⚠️ **裝上後請先填「服務的 QQ 群號」（`groups`）。** 留空表示**不處理任何群消息**（私聊仍然放行），機器人在群裡會完全不說話。

## 功能

- **群門禁**：只處理 `groups` 裡列出的群；其他群的消息直接丟棄。私聊總是放行。`ignored_senders` 裡的 QQ 號（比如群裡的其他機器人）完全忽略。
- **按場景選模型**：群聊、私聊可以各選一個模型；個別群還可以單獨指定模型（`群號=模型ID`）。沒選就用 AstrBot 預設模型。實現方式是把選中的模型寫進 AstrBot 的會話級模型偏好（和在 WebUI「會話管理」裡手動切換模型是同一處設定）。
- **喊名字當 @**：填了 `bot_name` 後，群友在句子裡明顯是在叫機器人（如「早啊<名字>」「<名字>你覺得呢」「問問<名字>」）會被當作 @；只是在聊它（「<名字>的…」「加<名字>好友」）或 @ 了別人時不算。同一個群 20 秒內最多觸發一次，防止刷屏。**`bot_name` 留空則不啟用這個功能。**
- **只 @ 一下 / 只喊一聲**：先等對方把話說完（最長約 25 秒），把隨後發的文字和圖片合成一條再交給模型；什麼都沒說就讓模型簡單回應一句。
- **表情包識別**：根據 NapCat 消息裡的 `sub_type` / `mface` / 「[动画表情]」標記判斷表情包，給模型加一句提示，讓它把表情包當語氣，不去描述畫面。
- **歷史圖片瘦身**：發給模型的歷史裡，只保留最近一條使用者消息的圖片，舊圖換成文字「[圖片]」，減少請求體積。
- **評價類請求提示**：消息裡有「點評 / 評價 / 你覺得…怎麼樣」之類時，提醒模型明確表態、給出理由。
- **攔截內部標記**：回覆裡如果混進了 `<system_reminder>` 或 DSML 工具呼叫標記，發送前刪掉；刪完為空就不發。
- **表情標籤兜底**：配合表情包插件 [meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) 使用時，模型偶爾會把「&&標籤&&」原樣留在回覆裡（比如調工具前單獨吐出的那一段，或整段只有標籤）。本插件在表情包插件之後再檢查一遍：把殘留的標籤換成該分類下隨機一張表情圖（讀取 `data/plugin_data/meme_manager/memes/<標籤>/`），沒有這個分類就直接刪掉；刪完為空就不發。沒裝表情包插件時不會有影響。

這個插件的配置同時也是「共用設定」：同套件的其他插件（帶有同一份 `lively_common.py` 的插件）沒有單獨填主人、群、機器人 QQ、名字、人設用詞、平台時，會來這裡取。單獨使用本插件不需要裝其他插件。

## 安裝

1. 在 AstrBot WebUI 的「插件市場」搜索安裝；或在「安裝插件」裡填本倉庫地址；或手動把本倉庫克隆到 `AstrBot/data/plugins/` 下後重啟 AstrBot。
2. 打開插件配置，至少填上 `groups`（要服務的群號）。
3. 其他項按需填寫，儲存後重載插件。

無第三方 Python 依賴。

## 配置

在 AstrBot WebUI 的插件配置頁填寫（也可以編輯 `data/config/<插件目錄名>_config.json`）。所有預設值都是空的。

| 配置項 | 說明 | 留空時 |
| --- | --- | --- |
| `owner_qq` | 主人的 QQ 號，可以填多個，第一個是主要主人（彙報、審核類消息私信給他） | 用 AstrBot 的管理員 ID（`admins_id`） |
| `groups` | 服務的 QQ 群號，只有這些群的消息會被處理 | **不處理任何群**（只私聊） |
| `bot_qq` | 機器人自己的 QQ 號 | 收到第一條消息後自動識別，或向 NapCat 查詢 |
| `bot_name` | 機器人的名字。群友在句子裡喊這個名字會被當作 @ | **不啟用喊名字當 @** |
| `self_call` | 機器人的自稱（同套件插件的提示詞和發給主人的消息裡用），例如「本喵」 | 我 |
| `owner_call` | 機器人對主人的稱呼，例如「老大」 | 主人 |
| `card_name` | 好感度插件那張卡的叫法 | 印象卡 |
| `owner_traditional` | 同套件插件發給主人的消息轉成繁體（需要 `opencc-python-reimplemented`） | 開啟 |
| `platform_id` | AstrBot 消息平台裡 aiocqhttp（NapCat）平台的 ID | 自動用第一個 aiocqhttp 平台 |
| `persona_id` | 機器人用的 AstrBot 人格 ID（同套件的其他插件後台生成內容時套用） | 用 AstrBot 預設人格 |
| `group_provider_id` | 群聊用的模型 | 用 AstrBot 預設模型 |
| `group_providers` | 個別群單獨用的模型，一行一個，格式 `群號=模型ID`，例如 `123456789=my-provider-2`。指定的模型不存在時退回 `group_provider_id` | 不單獨指定 |
| `dm_provider_id` | 私聊用的模型 | 用 AstrBot 預設模型 |
| `ignored_senders` | 完全忽略的 QQ 號，群裡的其他機器人填這裡，避免機器人之間對聊 | 不忽略任何人 |

說明：`bot_name` 在插件加載時讀取，修改後請重載插件。

## 兼容性

- AstrBot `>= 4.29.0`
- 消息平台：僅 `aiocqhttp`（OneBot v11），在 NapCat 上測試。表情包識別讀取的是 NapCat 的原始消息字段，其他 OneBot 實現可能識別不到表情包，但不影響其他功能。
- 用到的 AstrBot 介面：`astrbot.api` 下的公開介面，以及 `astrbot.core.agent.message.TextPart`（目前沒有公開導出）。AstrBot 大版本升級後如有變化會跟進。

## 隱私

插件本身不聯網、不收集資料。會話級模型偏好寫在 AstrBot 自己的資料庫裡。

## 作者與許可

作者：[GUCine](https://github.com/GUCine)

MIT License，Copyright (c) 2026 GUCine，見 [LICENSE](LICENSE)。
