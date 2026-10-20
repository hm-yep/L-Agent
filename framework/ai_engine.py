# -*- coding: utf-8 -*-
"""AI 引擎：DeepSeek API（真·自由对话版）

设计要点（不再是"预设指令的人机"）：
- 除工具调用（搜索/打开应用等必须走本地能力）外，用户说什么都由模型自己回答，
  包括闲聊、冷笑话、讲数学题、知识问答、学习教育等任意话题。
- 模型通过 JSON 返回动作序列；普通对话就是 {"action":"show_bubble","text":"模型的回答"}
- USE_REAL_AI=False 时降级为本地规则模拟（开发/无网络场景）。

AI 配置项在运行时从 framework.config 读取，可在设置界面修改并持久化：
    api_key / base_url / model

动作池（二十七个动作）:
    search_app / open_app / show_bubble / jump / think / show_card / move_to / idle
    / screenshot / set_volume / shutdown / restart / lock / cancel_shutdown
    / create_doc / see_screen / remember / show_plan / remind / todo_add / todo_done
    / todo_list / web_search / read_doc / work_log / run_workflow / analyze_data
"""

import json
import random
import re
import time
from urllib.parse import urlparse, urljoin

import requests
from PyQt5.QtCore import QObject, QThread, pyqtSignal

from framework import config
from framework.task_board import format_duration
from framework.utils import logger

# 哨兵：HTTP 错误已经通过 failed 信号上报，run() 不应再发 finished
_FAILED = object()

# 可重试的 HTTP 状态码（限流 / 网关抖动 / 服务端临时故障）
_RETRY_CODES = {429, 500, 502, 503, 504}

SYSTEM_PROMPT = """你是"凌零"——"凌零·桌面AI助手"，一位住在用户桌面上的AI助理（数字员工），
形象是一位身披银甲、佩剑的Q版小骑士，同时也是一个真正有能力的智能助理 Agent。

【身份规则（最高优先级）】
- 你的名字是"凌零"，身份是桌面AI助手/数字员工；
- 【绝不】在回复里自称"桌宠""宠物""皮皮"或任何其他名字，也绝不把自己说成玩具；
- 用户问"你是谁/你叫什么/介绍一下自己"时，大方回答："我是凌零，你的桌面AI助手，
  查资料、做文档、定提醒、管待办，都可以吩咐。"
- 骑士只外在形象与说话风格，自我认知永远是：凌零·桌面AI助手。

【你能做的事】
1. 本地工具能力（只能从下面动作池里选，用于查找/打开本机应用等实际操作）
2. 自由对话：聊天、情绪安慰、讲笑话、冷笑话、讲题（数学/物理/编程/英语等一切学科）、
   写文案、翻译、知识问答、学习辅导——这些内容由你自己生成，直接放进 show_bubble 的 text 里。

【返回格式】必须且只能返回一个 JSON 数组，不要任何解释文字、不要 markdown 标记。
参数名必须与下面完全一致：所有回答/闲聊/讲题都用 show_bubble 的 text 参数。
示例（闲聊）：[{"action": "show_bubble", "text": "我在，有何吩咐？"}]
示例（打开应用）：[{"action": "show_bubble", "text": "遵命，这就打开。"},
                 {"action": "open_app", "app": "微信"}]

【动作池】
1. {"action":"search_app","app":"应用名"}   在本机查找某个应用程序
2. {"action":"open_app","app":"应用名"}     在本机打开某个应用程序
3. {"action":"show_bubble","text":"要说的话"}  把你想说的话显示出来（所有回答都用这个）
4. {"action":"jump"}                        跳跃庆祝
5. {"action":"think"}                       思考
6. {"action":"show_card","title":"标题","path":"内容1","size":"内容2"}  展示信息卡片
7. {"action":"move_to","anchor":"top_left|top_right|bottom_left|bottom_right|center|taskbar"}  移动
8. {"action":"idle"}                        待机
9. {"action":"screenshot"}                  截取当前屏幕图片（截完自动弹窗让用户选保存位置）
10. {"action":"set_volume","volume":"0~100的数字","app":"应用名(可选)"}  调节音量：
    不填 app 调系统总音量；填 app（如"微信"）只调该应用的声音，不动系统音量
11. {"action":"shutdown","delay":"延时秒数(可选,默认60)"}   定时关机，delay 秒后执行
12. {"action":"restart","delay":"延时秒数(可选,默认60)"}    定时重启
13. {"action":"lock"}                        立即锁定屏幕（等同 Win+L）
14. {"action":"cancel_shutdown"}             取消已排定的关机/重启
15. {"action":"create_doc","doc_type":"ppt、doc、xlsx 或 html","filename":"文件名.扩展名","content":{...}}
    制作文档，由程序弹出保存对话框让用户选位置（html 生成后自动用浏览器打开）。content 结构按类型：
    ppt : {"title":"演示标题","subtitle":"副标题(可省)","template":"模板名(可省)",
           "slides":[{"title":"页标题","bullets":["要点1","要点2"],
                      "image_query":"英文搜索词(可选)",
                      "table":{"headers":["列1","列2"],"rows":[["a1","a2"],["b1","b2"]]}}]}
    模板名可填：深空蓝(默认) / 翡翠绿 / 暖橙 / 绛紫 / 雅灰——按主题气质挑一套；
    适合表格呈现的内容（数据对比、排期、名单、参数对照）给 table 字段，
    headers 是表头数组，rows 是行数组（每行一个数组，数字直接写数字），
    一页表格别超过 8 行；要点和表格可以同页；结尾致谢页程序会自动生成，不用自己写。
    doc : {"title":"文档标题","sections":[{"heading":"章节标题","paragraphs":["段落"],"table":{"headers":["列"],"rows":[["单元格"]]}}]}
    xlsx: {"sheets":[{"name":"工作表名","headers":["列头1","列头2"],"rows":[["行数据1","行数据2"]]}]}
    html: {"title":"网页标题","subtitle":"副标题(可省)","sections":[{"heading":"章节标题","paragraphs":["要点或段落"],"table":{"headers":["列"],"rows":[["单元格"]]}}]}
          适合图文并茂的调研报告/汇报页，结构与 doc 相同、展示更像网页
16. {"action":"see_screen","question":"用户想问屏幕的问题"}  截取用户屏幕并用视觉模型"看"后回答，
    适合"看看我屏幕上是什么""这个报错怎么回事""我屏幕上这个页面怎么样"等请求
17. {"action":"remember","fact":"要长期记住的一句话"}        把用户告诉你的重要信息写入长期记忆
    （偏好、称呼、习惯等），fact 用简短的陈述句，如"用户喜欢深色风格的PPT"
18. {"action":"show_plan","steps":["第一步","第二步","第三步"]}  拟定多步任务计划（步骤 3~6 条）。
    注意：只返回 show_plan，等待用户在计划卡上确认，【绝不要】在同一回复里直接开始执行任何动作；
    用户点确认后系统会发来「计划执行」请求，那时你才逐步调用工具动作，做完的步骤不要重复做；
    用户对计划提出补充/修改意见时，返回修订后的 show_plan（同样只返回计划）。
19. {"action":"remind","text":"提醒内容","seconds":数字}     定时提醒，seconds 为延时的秒数。
    换算必须准确：20秒=20、1分25秒=85、半小时=1800、2小时=7200；用户没说多久就填 1800
20. {"action":"todo_add","text":"待办内容"}                  往待办清单加一条
21. {"action":"todo_done","index":数字}                      完成待办，index 为待办序号（1 起）
22. {"action":"todo_list"}                                   展示当前待办清单
23. {"action":"web_search","query":"搜索关键词","count":条数(可省,默认5)}
    联网搜索最新资料。适合"查一下/调研/最新消息/数据从哪来"等需要实时信息的请求，
    也用于为报告/PPT 收集素材。搜到的资料会自动进入你的工作上下文（【检索资料】），
    后续轮次可直接引用，不要在气泡里复述网址。
24. {"action":"read_doc","path":"文件的完整路径"}            阅读用户本机的文档
    （支持 txt/md/csv/json/docx/pptx/xlsx），内容会进入你的工作上下文（【检索资料】），
    之后你可以总结、答问、或基于它写新文档。
25. {"action":"work_log"}                                    播报你今天的工作日志
    （替用户做了哪些事），适合"你今天干了啥/汇报一下工作"
26. {"action":"run_workflow","name":"工作流模板名"}          一键启动预设工作流
    （多步编排：调研->整理->交付成文档），name 从【工作流模板】一节里选，
    适合"来份早报""跑个工作流""按模板来一份"这类请求
27. {"action":"analyze_data","path":"表格文件的完整路径","group_by":"分组列(可省)",
    "x_col":"横轴列(可省)","y_col":"纵轴数值列(可省)","chart":"bar、line 或 pie(可省)"}
    用 pandas 分析用户本机的表格数据（csv / tsv / xlsx）：算出规模、缺失、数值列
    合计与分布、类别频次、分组名次与相关性，并出一张图（柱状/折线/饼图）。
    适合"分析一下这份表/哪个区域最高/帮我看看数据/画个趋势图"这类请求，
    用户给的是文件路径而不是网页资料时【优先用它】，不要改用 read_doc 硬读文字。
    chart 填"不要"就只给文字结论；结论会自动进入你的工作上下文，
    之后你可以直接讲给用户，不要在气泡里复述全部数字。

【重要规则】
- 用户说"帮我找xx""xx在哪里"：【必须】带上 search_app 动作
- 用户说"打开xx""启动xx"：【必须】在动作序列里带上 open_app 动作，
  绝不能只回一句"好的这就打开"却不给动作。标准序列：
  show_bubble 确认 + open_app + jump
- 用户说"截图""截屏""帮我截图"：【必须】带上 screenshot 动作，不能只说话。
  标准序列：show_bubble 确认 + screenshot
- 用户说"调音量""把音量调到xx"：【必须】带上 set_volume 动作，volume 填 0~100 数字。
- 用户说"把xx的音量调到xx"：【必须】带上 set_volume 并填 app 参数（只调该应用，不动系统音量）。
  标准序列：show_bubble 确认 + set_volume
- 用户说"关机""关电脑""帮我关机"：【必须】带上 shutdown 动作，
  标准序列：show_bubble 确认 + shutdown。不要自己编 delay，
  让程序走默认延时（用户可取消）。
- 用户说"重启""重启电脑"：【必须】带上 restart 动作，
  标准序列：show_bubble 确认 + restart。
- 用户说"锁屏""锁一下屏幕"：【必须】带上 lock 动作，
  标准序列：show_bubble 确认 + lock。
- 用户说"取消关机""别关机了"：【必须】带上 cancel_shutdown 动作。
- 关机/重启的确认语要提醒用户"有 60 秒可以反悔，说'取消关机'即可撤销"。
- 用户说"做个PPT""生成幻灯片""做个演示文稿"：【必须】带上 create_doc 动作（doc_type 填 "ppt"），
  由你在 content 里拟好完整内容：每页标题 + 3~5 条短要点（不要大段文字），最多 10 页。
  标准序列：show_bubble 确认 + create_doc。
- 用户说"写个文档""写份报告""写篇文章"：【必须】带上 create_doc 动作（doc_type 填 "doc"），
  content 里分章节给段落，需要数据时可在章节里附 table。
- 用户说"生成表格""做个Excel""整理个名单"：【必须】带上 create_doc 动作（doc_type 填 "xlsx"），
  content 里给表头和行数据，数字字段直接写数字或数字字符串。
- create_doc 的 filename 只填文件名（如"学习计划.pptx"），保存位置由程序弹窗让用户选，
  不要在 filename 里写盘符或路径。
- create_doc 做 PPT 时内容必须充实饱满：
  正文一般 6~10 页（封面和致谢页由程序自动生成，不用你写）；
  每页 3~6 条要点，每条是 12~24 字的完整陈述句，尽量带数字、对比或
  具体举措（如"新客户同比增长 40%，主要来自华南区域"），
  禁止三五个字的干瘪短语，禁止"各种要点""相关内容"这类空话；
  有数据支撑的内容（对比/排期/名单/参数）至少安排 1~2 页用 table 字段呈现；
  若某页适合配图，可给该页加 image_query（2~4 个英文搜索词），
  没配图库 Key 时程序会自动忽略；template 字段给一套模板名
  （深空蓝/翡翠绿/暖橙/绛紫/雅灰，按主题气质选）。
- 用户一句话安排了多件有先后顺序的事（"先A，再B，最后C"）：【必须】只返回
  show_plan（按顺序拆成 2~6 步，一步一件事，步骤写具体动作）+ 一句 show_bubble
  说明，等用户在计划卡确认后系统会逐步推进；绝不在同一回复里把几件事一起做。
- create_doc 支持按用户要求定制样式：用户说"标题红色/换个颜色/字大一点/
  行距1.5/用宋体"等，在 create_doc 里加与 content 平级的 "style" 字段
  （按需给，别硬凑）：{"title_color":"红色 或 #FF0000","accent_color":"蓝色",
  "title_size":36,"body_size":16,"line_spacing":1.5,"font":"微软雅黑"}。
  颜色认中文色名或#十六进制；字号是数字；line_spacing 是 1.0~3.0 的倍数。
- 用户说"保存到桌面/存到文档里/放下载里"：create_doc 加 "location":"desktop"
  或 "documents"/"downloads"，程序直接存到对应位置、不再弹保存对话框；
  用户没提位置就不要加 location（保持弹窗由用户自己选）。
- 用户说"看看屏幕""屏幕上是什么""帮我看看这个报错/页面"：【必须】带上 see_screen
  动作，question 填用户想知道的问题。标准序列：show_bubble 确认 + see_screen。
- 用户说"记住……"或告诉你个人信息/偏好（"我叫X""我喜欢深色PPT"）：
  【必须】带上 remember 动作，fact 用一句简短陈述句。
- 复杂任务（"帮我准备明天的汇报""调研并出个报告"等需要多步完成的）：
  【必须】只返回 show_plan（3~6 步，具体可执行，不要写空话）+ 一句 show_bubble 说明，
  然后停下等用户在计划卡上确认；绝不在同一回复里直接开始执行。
  收到以「计划执行」开头的用户消息时，才是用户已确认，请调用当前未完成步骤对应的工具动作；
  已打勾的步骤不要重复做；一轮做不完，做完当前步骤即可，系统会让你继续。
- 用户对计划说"第X步改成……""再详细点"等修改意见：返回修订后的 show_plan（只返回计划）。
- 用户说"X秒/X分钟/X小时后提醒我……"（支持复合时长，如"1分25秒"）：
  【必须】带上 remind 动作，seconds 填准确换算的秒数（1分25秒=85），不要四舍五入成分钟，
  text 填要提醒的事（不要把时间词写进 text）。标准序列：show_bubble 确认 + remind。
  一条消息只设一个提醒，绝不要返回两个 remind 动作。
- 用户说"加个待办""我还要做XX"：带上 todo_add；"做完了XX/完成第N条"：
  带 todo_done；"看待办/还有什么要做的"：带 todo_list。
- 用户说"搜一下/查一下/调研XX/XX的最新消息"，或做报告、PPT 前需要真实资料：
  【必须】带上 web_search 动作，query 提炼成精炼的搜索词。
  标准序列：show_bubble 确认 + web_search。资料返回后系统会再向你提问，
  那时再基于【检索资料】回答或写文档；引用时注明来源网站名，绝不要编造数据。
- 收到【检索资料】先做质量判断：资料与任务明显不相关或过于单薄时，
  【必须】换更精确的关键词重新 web_search（或在计划轮返回修订版 show_plan
  调整步骤），绝不硬凑内容；实在查不到就如实说明资料局限，不要编造。
- 用户说"读一下/总结这份文件"并给了文件路径：【必须】带上 read_doc 动作，
  path 填完整路径。标准序列：show_bubble 确认 + read_doc。
- 用户说"你今天干了啥/汇报一下你的工作"：【必须】带上 work_log 动作。
- 用户说"来份早报""跑个工作流""执行工作流XX"或点名某个模板：【必须】带上
  run_workflow 动作，name 从【工作流模板】里挑最贴近的一个；
  一个模板也没匹配上时，用 show_bubble 列出现成的模板名供用户挑。
- 除此之外的一切问题（"讲个笑话""1+1等于几""帮我解这道方程""今天心情不好""讲讲相对论"）：
  你直接用自己的知识回答，用 show_bubble 输出完整答案。禁止回复"我只会这些指令""无法回答"这类话。
- 讲数学题/写代码题时：text 里写清楚步骤，控制在 120 字以内，优先给结论再给简要过程
- 讲笑话时：直接讲一个具体笑话，不要说"这是一个笑话"
- 你的性格：{personality}
- 说话风格必须像一位骑士：干脆利落、言出必行、有担当（例如"遵命""这就去办""任务完成""交给我"），
  可以幽默，但【禁止】软萌语气词（呀/啦/哦/哟/嘛/嗯嗯）和波浪号"~"，不撒娇、不卖萌、不用可爱表情
- 用中文回答，语气自然，像一位可靠的骑士伙伴"""


class AIWorker(QThread):
    """AI 请求线程（避免阻塞 UI 主线程）"""

    finished = pyqtSignal(list)      # 成功：动作列表
    failed = pyqtSignal(str)         # 失败：错误信息

    def __init__(self, message: str, history: list, personality: str,
                 parent=None, facts: str = "", notes: str = ""):
        super().__init__(parent)
        self.message = message
        self.history = history
        self.personality = personality
        self.facts = (facts or "").strip()   # 长期记忆（注入系统提示词）
        self.notes = (notes or "").strip()   # 检索资料（搜索/读文档的成果）
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        try:
            actions = self._request_deepseek()
        except Exception as e:  # noqa: BLE001
            logger.error(f"[AI] 请求异常: {e}")
            if not self._stopped:
                self.failed.emit(str(e))
            return
        if self._stopped:
            return
        if actions is _FAILED:
            return          # HTTP 错误已通过 failed 信号上报，别再发 finished
        if actions is None:
            # 网络层重试耗尽 -> 降级本地模拟（离线兜底）
            actions = mock_ai(self.message, self.personality)
        self.finished.emit(actions)

    # ------------------------------------------------------------
    def _messages(self) -> list:
        """构造完整消息上下文"""
        # 注意：SYSTEM_PROMPT 内含大量 JSON 花括号，不能用 .format()，
        # 否则 {"action"...} 会被当成占位符抛 KeyError。用 replace 注入性格。
        style = config.PERSONALITIES.get(
            self.personality, config.PERSONALITIES["活泼"]
        )["chat_style"]
        prompt = SYSTEM_PROMPT.replace("{personality}", style)
        if self.facts:
            # 长期记忆：用户明确让记住的事，回答时可自然运用
            prompt += ("\n\n【长期记忆】下面是用户曾告诉你的信息，"
                       "回答时可自然运用（不必逐条复述）：\n" + self.facts)
        if self.notes:
            # 检索资料：web_search / read_doc 拿到的素材，回答/写文档时引用
            prompt += ("\n\n【检索资料】下面是你刚通过联网搜索或阅读本机文档"
                       "得到的资料，回答用户问题或写文档时优先基于这些真实内容，"
                       "引用时注明来源网站名，资料没覆盖的部分才用自己的知识"
                       "（不要伪装成资料里的数据）：\n" + self.notes)
        if config.WORKFLOW_TEMPLATES:
            # 工作流模板清单：让 run_workflow 有名可挑
            lines = "\n".join(f"- {t['name']}：{t['desc']}"
                              for t in config.WORKFLOW_TEMPLATES)
            prompt += ("\n\n【工作流模板】以下一键工作流可用 run_workflow "
                       "直接启动（系统会自动按模板编排多步任务）：\n" + lines)
        messages = [{"role": "system", "content": prompt}]
        messages.extend(self.history[-config.MEMORY_MAX_TURNS * 2:])
        messages.append({"role": "user", "content": self.message})
        return messages

    def _request_deepseek(self):
        """
        调用 DeepSeek API，带自动重试（网络抖动/限流/JSON损坏/截断都会重试）。
        :return: 动作列表；网络层彻底失败返回 None（上层降级本地模拟）；
                 HTTP 错误已上报 failed 时返回 _FAILED 哨兵。
        """
        if not config.USE_REAL_AI or not config.DEEPSEEK_API_KEY:
            return mock_ai(self.message, self.personality)

        url = normalize_endpoint(config.DEEPSEEK_BASE_URL)
        headers = {
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.DEEPSEEK_MODEL,
            "messages": self._messages(),
            "temperature": 0.9,
            # 文档生成要输出整份结构化内容（6~10 页 PPT/多章节），额度放宽
            "max_tokens": max(config.AI_MAX_TOKENS, 6144)
                          if is_doc_request(self.message)
                          else config.AI_MAX_TOKENS,
        }

        attempts = config.AI_MAX_RETRIES + 1
        last_err = ""
        for attempt in range(attempts):
            if self._stopped:
                return _FAILED
            try:
                resp = requests.post(url, headers=headers, json=payload,
                                     timeout=config.AI_TIMEOUT)
                if resp.status_code in _RETRY_CODES:
                    last_err = f"HTTP {resp.status_code}"
                    logger.warning(f"[AI] 第{attempt + 1}/{attempts}次请求"
                                   f" {last_err}，稍后重试")
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code == 401:
                    self.failed.emit("API Key 无效或已过期，请检查设置")
                    return _FAILED
                if resp.status_code == 402:
                    self.failed.emit("账号余额不足，请充值或更换 Key")
                    return _FAILED
                resp.raise_for_status()

                choice = (resp.json().get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content", "")
                finish = choice.get("finish_reason", "")

                if not str(content).strip():
                    last_err = "模型返回空内容"
                    logger.warning(f"[AI] {last_err}，"
                                   f"重试 {attempt + 1}/{attempts}")
                    continue
                if finish == "length":
                    # 回答被 max_tokens 截断：JSON 必然残缺，放大额度重试
                    payload["max_tokens"] = min(16384,
                                                payload["max_tokens"] * 2)
                    last_err = "回答被截断"
                    logger.warning(f"[AI] 回答被截断，加大 max_tokens 到"
                                   f" {payload['max_tokens']} 重试")
                    continue

                actions = parse_ai_content(content)
                logger.info(f"[AI] DeepSeek 返回 {len(actions)} 个动作")
                return actions

            except (json.JSONDecodeError, ValueError) as e:
                # JSON 残缺（截断/夹杂解释文字）：重试往往能拿到干净结果
                last_err = f"返回解析失败: {e}"
                logger.warning(f"[AI] {last_err}，重试 {attempt + 1}/{attempts}")
                continue
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else "?"
                last_err = f"HTTP {code}"
                logger.error(f"[AI] DeepSeek HTTP错误 {code}: {e}")
                if code in _RETRY_CODES and attempt < attempts - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                self.failed.emit(f"请求失败（HTTP {code}）")
                return _FAILED
            except requests.RequestException as e:
                last_err = str(e)
                logger.warning(f"[AI] 网络异常（第{attempt + 1}/{attempts}次）"
                               f": {e}")
                time.sleep(1.5 * (attempt + 1))
                continue

        # 重试耗尽：区分"网络不通"（降级离线模拟）和"内容损坏"（明确告知重问）
        logger.error(f"[AI] 请求失败（已重试{attempts}次）: {last_err}")
        if "解析失败" in last_err or "截断" in last_err or "空内容" in last_err:
            self.failed.emit("我刚才没想好怎么回答，请再问一次试试~")
            return _FAILED
        return None


def normalize_endpoint(url: str) -> str:
    """
    规范化用户填写的接口地址：
    支持直接填 https://api.deepseek.com 或完整 .../chat/completions，
    也支持其他 OpenAI 兼容网关。
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return config.DEEPSEEK_BASE_URL
    if url.endswith("/chat/completions"):
        return url
    # 判断是否是 OpenAI 兼容网关（含 /v1 前缀）
    path = urlparse(url).path
    if path.endswith("/v1"):
        return urljoin(url + "/", "chat/completions")
    return urljoin(url + "/", "v1/chat/completions")


def parse_ai_content(content) -> list:
    """解析 AI 返回内容为动作列表（容错：剥掉 markdown 代码块）"""
    if isinstance(content, list):
        return content
    text = str(content).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                raise ValueError(f"AI 返回非 JSON: {text[:80]}")
        else:
            # 模型可能直接返回了纯文本回答：包装成气泡
            return [{"action": "show_bubble", "text": text}]

    if isinstance(data, dict):
        data = data.get("actions", [data])
    if not isinstance(data, list):
        raise ValueError("AI 返回格式错误")
    return data


def test_connection(api_key: str, base_url: str, model: str) -> tuple:
    """
    测试 API 连通性（设置界面"测试连接"按钮用）。
    :return: (成功与否, 提示文本)
    """
    if not api_key:
        return False, "请先填写 API Key"
    url = normalize_endpoint(base_url)
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": model,
                  "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 5},
            timeout=20,
        )
        if resp.status_code == 200:
            return True, f"连接成功（{model}）"
        if resp.status_code == 401:
            return False, "API Key 无效"
        if resp.status_code == 402:
            return False, "余额不足"
        return False, f"连接失败（HTTP {resp.status_code}）"
    except requests.RequestException as e:
        return False, f"网络异常：{type(e).__name__}"


# ============================================================
# 模拟 AI（离线降级，不依赖真实 API）
# ============================================================
_OPEN_WORDS = ("打开", "启动", "运行", "开一下")
_FIND_WORDS = ("找", "查找", "在哪", "搜索", "找到")
_WEATHER_WORDS = ("天气", "气温", "下雨", "温度")
_MATH_HINT = re.compile(r"[0-9+\-*/×÷=]")

_PERSONA_TAILS = {
    "活泼": ["！", "，小事一桩！", "，看我的！"],
    "沉稳": ["。", ""],
    "毒舌": ["，行吧。", "，这都不会？", "，勉强帮你。"],
}


def _extract_app_name(text: str) -> str:
    """从'打开微信'/'找一下计算器在哪'中提取应用名"""
    for w in _OPEN_WORDS + _FIND_WORDS:
        if w in text:
            name = text.replace(w, " ")
            for tail in ("在哪", "一下", "帮我", "请问", "呢", "？", "?",
                         "吧", "啦", "应用", "程序"):
                name = name.replace(tail, " ")
            name = name.strip()
            if name:
                return name.split()[0]
    return text.strip()


def _mock_answer(text: str, personality: str) -> str:
    """离线模式下的通用回答（真实 API 可用时不走这里）"""
    tail = random.choice(_PERSONA_TAILS.get(personality, [""]))

    # 简单算术：本地算出结果，显得不是纯人机
    if _MATH_HINT.search(text):
        expr = text.replace("等于多少", "").replace("是多少", "").strip("?？ ")
        expr = (expr.replace("×", "*").replace("x", "*").replace("X", "*")
                    .replace("÷", "/").replace("加", "+").replace("减", "-")
                    .replace("乘以", "*").replace("除以", "/"))
        if re.fullmatch(r"[\d\s+\-*/().]+", expr):
            try:
                val = eval(expr, {"__builtins__": {}})  # noqa: S307 受限求值
                return f"{expr} = {val}{tail}（离线模式，联网后我能一步步讲给你听）"
            except Exception:  # noqa: BLE001
                pass
    if "笑话" in text:
        return ("程序员最讨厌两件事：别人不写注释，和让自己写注释" + tail
                + "（离线模式，联网后我给你现编一个）")
    return random.choice([
        "我在，请讲。（离线模式，联网后什么都能聊）",
        "这个问题有点难度，联网后我慢慢讲给你听" + tail,
        "现在未联网，此事先记下了。（离线模式）",
    ])


# 截图意图关键词（意图兜底与离线模拟共用，含口语变体）
_SHOT_WORDS = ("截图", "截个图", "截张图", "截屏", "屏幕快照")

# 文档生成意图关键词：整份 PPT/文档的内容远超普通回复的长度，
# 命中这些词时本次请求的 max_tokens 下限提到 4096，防止 JSON 被截断
_DOC_HINT_WORDS = ("ppt", "幻灯片", "演示文稿", "word", "文档", "报告",
                   "excel", "表格", "名单", "报表")


def is_doc_request(message: str) -> bool:
    """判断用户消息是否像是在要生成文档（只用于放大 max_tokens，宽松匹配）"""
    text = (message or "").lower()
    return any(w in text for w in _DOC_HINT_WORDS)

# 电源意图关键词（意图兜底与离线模拟共用；取消优先级最高）
_POWER_SHUTDOWN_WORDS = ("关机", "关电脑", "关闭电脑", "关一下电脑")
_POWER_RESTART_WORDS  = ("重启", "重新启动", "重启电脑", "重启一下")
_POWER_LOCK_WORDS     = ("锁屏", "锁定屏幕", "锁一下屏", "锁一下屏幕")
_POWER_CANCEL_WORDS   = ("取消关机", "取消重启", "别关机", "不要关机")

# 屏幕视觉理解意图（"看屏幕"类；与"截图"分开：截图是保存图片）
_VISION_WORDS = ("看看屏幕", "看屏幕", "看看我的屏幕", "看下屏幕",
                 "看一下屏幕", "屏幕上是什么", "屏幕上有什么",
                 "识别屏幕", "看看屏幕上", "屏幕上这个",
                 "帮我看看屏幕", "看看电脑屏幕", "看下电脑屏幕",
                 "瞧瞧屏幕", "瞅一眼屏幕", "看一眼屏幕",
                 "屏幕上显示", "屏幕上写的什么", "这是什么界面")

# 定时提醒意图
_REMIND_WORDS = ("提醒我", "设个提醒", "定个提醒", "定个闹钟", "别让我忘了",
                 "到点叫我")
_REMIND_NOISE = ("提醒我", "设个提醒", "定个提醒", "定个闹钟", "别让我忘了",
                 "到点叫我", "提醒", "一下", "帮我", "麻烦", "请", "之后",
                 "以后", "吧", "哦", "。", "，", "！")

# 待办清单意图（完成 > 列出 > 新增，匹配顺序不能反）
_TODO_DONE_WORDS = ("完成待办", "待办完成", "做完待办", "办完待办",
                    "完成第", "做完第", "划掉第")
_TODO_LIST_WORDS = ("看待办", "查待办", "待办清单", "有什么待办", "我的待办",
                    "要做的事", "任务清单", "有什么要做的", "待办有哪些")
_TODO_ADD_WORDS  = ("加个待办", "加待办", "加一条待办", "添加待办", "新增待办",
                    "记个待办", "待办加上", "加个任务", "记一下我要")

# 多步任务规划意图（仅供离线模拟；真实 AI 由提示词驱动 show_plan）
_PLAN_WORDS = ("帮我准备", "帮我规划", "规划一下", "拆解一下", "分几步")

# 多步任务拆分："先A，再B，最后C" -> 逐段计划（意图兜底与离线模拟共用）
_MULTI_TASK_MARKERS = ("先", "然后", "接着", "最后", "其次", "之后", "再")
_MULTI_TASK_SPLIT_RE = re.compile(
    r"首先|然后|接着|其次|最后|之后|先|再|，|,|；|;|。|！|!|？|\?|\n")


def split_multi_task(text: str) -> list:
    """把"先A，再B，最后C"式的一句话拆成步骤列表；不是多步任务返回 []"""
    t = (text or "").strip()
    if not t:
        return []
    if sum(t.count(w) for w in _MULTI_TASK_MARKERS) < 2:
        return []                      # 至少"先…再…"两段才算多步任务
    parts = [p.strip(" 　、.:：'\"“”·")
             for p in _MULTI_TASK_SPLIT_RE.split(t)]
    steps = []
    for p in parts:
        p = p.rstrip("。？！?!")
        if len(p) < 2:
            continue
        steps.append(p[:30])
        if len(steps) >= 6:
            break
    return steps if len(steps) >= 2 else []


# 样式要求提取（离线模拟用；真实模型按提示词直接产 style 字段）
_STYLE_COLOR_WORDS = {"红": "红色", "蓝": "蓝色", "绿": "绿色", "紫": "紫色",
                      "橙": "橙色", "金": "金色", "黑": "黑色", "灰": "灰色",
                      "粉": "粉色", "黄": "黄色"}
_FONT_WORDS = ("微软雅黑", "宋体", "黑体", "楷体", "仿宋", "隶书")


def _extract_style_hints(text: str) -> dict:
    """从用户原话里提取样式要求 -> create_doc 的 style 字段（没有要求给 {}）"""
    hints = {}
    m = re.search(r"标题[^。；,，]*?(红|蓝|绿|紫|橙|金|黑|灰|粉|黄)色?|"
                  r"(红|蓝|绿|紫|橙|金|黑|灰|粉|黄)色?的?标题", text or "")
    if m:
        hints["title_color"] = _STYLE_COLOR_WORDS[m.group(1) or m.group(2)]
    m = re.search(r"标题[^。；,，]*?(\d{1,2})\s*号|字号\s*(\d{1,2})", text or "")
    if m:
        size = int(m.group(1) or m.group(2))
        if 8 <= size <= 96:
            hints["title_size"] = size
    m = re.search(r"(\d(?:\.\d)?)\s*倍行距|行距\s*(\d(?:\.\d)?)", text or "")
    if m:
        try:
            ls = float(m.group(1) or m.group(2))
            if 1.0 <= ls <= 3.0:
                hints["line_spacing"] = ls
        except (TypeError, ValueError):
            pass
    for f in _FONT_WORDS:
        if f in (text or ""):
            hints["font"] = f
            break
    return hints


def _extract_location(text: str) -> str:
    """从用户原话里识别保存位置（desktop/documents/downloads，没有给空串）"""
    t = text or ""
    if "桌面" in t and re.search(r"存|保存|放|生成", t):
        return "desktop"
    if "下载" in t and re.search(r"存|保存|放", t):
        return "downloads"
    if re.search(r"(存|保存|放).{0,3}文档(里|夹)", t):
        return "documents"
    return ""


# 时长单位 -> 秒（长词在前，避免"分钟"被"分"截走）
_TIME_UNIT_SEC = {"小时": 3600, "钟头": 3600, "分钟": 60, "秒钟": 1,
                  "分": 60, "秒": 1}


def extract_remind(text: str) -> tuple:
    """
    从'10分钟后提醒我开会'里解析 (延时秒数, 提醒内容)。

    支持复合时长（"1分25秒"=85 秒、"1小时30分"=5400 秒）、
    单位秒/分钟/小时、"半小时"与中文数字（"十分钟"）。
    秒数解析不出返回 None（上层用默认值）；内容做尽力清理，
    匹配到的所有时间段都会从内容里剔除。
    """
    import re as _re
    total = 0.0
    spans = []
    # 阿拉伯数字 + 单位（可连续多段组合：1小时30分25秒）
    for m in _re.finditer(
            r"(\d+(?:\.\d+)?)\s*(小时|钟头|分钟|秒钟|分|秒)(?:之?后|过后|以后)?",
            text):
        total += float(m.group(1)) * _TIME_UNIT_SEC[m.group(2)]
        spans.append(m.span())
    if not total:
        m = _re.search(r"半\s*个?小时(?:之?后)?", text)
        if m:
            total, spans = 1800.0, [m.span()]
        else:
            m = _re.search(r"([零一二两三四五六七八九十]+)\s*"
                           r"(小时|钟头|分钟|分)(?:之?后)?", text)
            if m:
                n = _cn_to_int(m.group(1))
                if n:
                    total = float(n * _TIME_UNIT_SEC[m.group(2)])
                    spans = [m.span()]
    # 内容清理：删掉所有匹配到的时间段 + 噪声词（复合时长会产生多段）
    content = text
    for start, end in reversed(spans):
        content = content[:start] + " " + content[end:]
    for w in _REMIND_NOISE:
        content = content.replace(w, " ")
    content = _re.sub(r"^\s*之?后", "", content)   # 残留的"后"（"10分 后"）
    content = content.strip(" 　，。！？、的：:")
    return ((total if total else None), content or text.strip())

# 音量意图
_VOLUME_WORDS = ("音量", "声音调", "调声音")
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_VOLUME_APP_NOISE = ("请帮我", "帮我", "麻烦", "帮忙", "请", "把", "将",
                     "给", "调节", "调整", "调一下", "调", "设置", "设")


def _cn_to_int(text: str):
    """中文数字（一~九十九）转整数；不是中文数字返回 None"""
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    if text == "十":
        return 10
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        if (not head or head in _CN_DIGITS) and                 (not tail or tail in _CN_DIGITS):
            return tens * 10 + ones
    return None


def _extract_volume_number(text: str):
    """从'调到50'/'音量50%'/'百分之三十'里提取目标音量，没有返回 None"""
    import re as _re
    m = _re.search(r"(\d{1,3})\s*%?", text)
    if m:
        return int(m.group(1))
    m = _re.search(r"百分之([零一二两三四五六七八九十]+)", text)
    if m:
        return _cn_to_int(m.group(1))
    m = _re.search(r"(?:调|设)到([零一二两三四五六七八九十]+)", text)
    if m:
        return _cn_to_int(m.group(1))
    m = _re.search(r"音量([零一二两三四五六七八九十]+)", text)
    if m:
        return _cn_to_int(m.group(1))
    return None


def _extract_volume_app(text: str) -> str:
    """从'把微信的音量调到30'里提取应用名；系统音量（没提应用）返回空串"""
    import re as _re
    m = _re.search(r"([一-龥A-Za-z0-9]+?)的?音量", text)
    if not m:
        return ""
    name = m.group(1)
    changed = True
    while changed and name:
        changed = False
        for noise in _VOLUME_APP_NOISE:
            if name.startswith(noise):
                name = name[len(noise):]
                changed = True
    if name in ("系统", "电脑", "总体", "全局", "总音量"):
        return ""
    return name


def mock_ai(message: str, personality: str) -> list:
    """开发/离线模式模拟 AI：工具意图走规则，其余给通用回答"""
    text = message.strip()
    tail = random.choice(_PERSONA_TAILS.get(personality, [""]))

    # ---- 多步任务："先A，再B，最后C" -> 拆计划等确认（离线演示）----
    steps = split_multi_task(text)
    if steps:
        return [
            {"action": "show_bubble",
             "text": f"遵命，我把这几件事拆成了计划{tail}，你在计划卡上点"
                     "「开始执行」我就一步步来。"},
            {"action": "show_plan", "steps": steps},
        ]

    # ---- 自我介绍（身份认知：凌零·桌面AI助手，绝不是"桌宠"）----
    if any(w in text for w in ("你是谁", "你叫什么", "你的名字",
                               "介绍一下你自己", "自我介绍")):
        return [
            {"action": "show_bubble",
             "text": "我是凌零，你的桌面AI助手。查资料、做文档、定提醒、"
                     "管待办，吩咐一声就好。"},
        ]

    # ---- 电源操作（优先级最高：取消 > 锁屏 > 重启 > 关机）----
    if any(w in text for w in _POWER_CANCEL_WORDS):
        return [
            {"action": "show_bubble", "text": "遵命，已取消关机/重启。"},
            {"action": "cancel_shutdown"},
        ]
    if any(w in text for w in _POWER_LOCK_WORDS):
        return [
            {"action": "show_bubble", "text": "遵命，这就锁屏。"},
            {"action": "lock"},
        ]
    if any(w in text for w in _POWER_RESTART_WORDS):
        return [
            {"action": "show_bubble",
             "text": "遵命，60 秒后重启。想反悔就说「取消关机」。"},
            {"action": "restart"},
        ]
    if any(w in text for w in _POWER_SHUTDOWN_WORDS):
        return [
            {"action": "show_bubble",
             "text": "遵命，60 秒后关机。想反悔就说「取消关机」。"},
            {"action": "shutdown"},
        ]

    # ---- 工作流模板（离线兜底；模板命令本身不能再触发本分支，防死循环）----
    if text.startswith("执行工作流"):
        # 模板命令：拆成计划等用户确认，确认后按步骤走正常执行链
        body = text.split("：", 1)[-1] if "：" in text else ""
        steps = []
        for part in re.split(r"[;；]", body):
            part = re.sub(r"^第?[0-9一二三四五六七八九十]+步[:：,，、]?\s*",
                          "", part.strip()).strip()
            part = part.rstrip("。")
            if part:
                steps.append(part)
        return [
            {"action": "show_bubble",
             "text": "工作流收到，我拆成了计划，你确认后我就开跑。"},
            {"action": "show_plan", "steps": steps or
             ["收集资料", "整理内容", "产出成果并交付"]},
        ]
    if "工作流" in text or "早报" in text:
        hit = next((t for t in config.WORKFLOW_TEMPLATES if t["name"] in text),
                   None)
        if hit:
            return [
                {"action": "show_bubble",
                 "text": f"遵命，启动工作流「{hit['name']}」{tail}"},
                {"action": "run_workflow", "name": hit["name"]},
            ]
        names = "、".join(t["name"] for t in config.WORKFLOW_TEMPLATES)
        return [
            {"action": "show_bubble",
             "text": f"现成的工作流模板有：{names}。点名一个我就开跑。"},
        ]

    if any(w in text for w in _SHOT_WORDS):
        return [
            {"action": "show_bubble", "text": f"遵命，这就截图{tail}"},
            {"action": "screenshot"},
        ]
    if any(w in text for w in _VOLUME_WORDS):
        vol = _extract_volume_number(text)
        if vol is not None:
            app = _extract_volume_app(text)
            if app:
                return [
                    {"action": "show_bubble",
                     "text": f"遵命，只调{app}的声音到{vol}，系统音量不动。"},
                    {"action": "set_volume", "volume": vol, "app": app},
                ]
            return [
                {"action": "show_bubble", "text": f"遵命，系统音量调到{vol}。"},
                {"action": "set_volume", "volume": vol},
            ]

    # ---- 屏幕视觉理解 ----
    if any(w in text for w in _VISION_WORDS):
        return [
            {"action": "show_bubble", "text": f"遵命，我这就看看你的屏幕{tail}"},
            {"action": "see_screen", "question": text},
        ]
    # ---- 定时提醒（seconds 精确到秒，支持复合时长"1分25秒"）----
    if any(w in text for w in _REMIND_WORDS):
        seconds, content = extract_remind(text)
        secs = int(seconds) if seconds \
            else config.REMINDER_DEFAULT_MINUTES * 60
        wait = format_duration(secs)
        return [
            {"action": "show_bubble",
             "text": f"遵命，{wait}后提醒你：{content}"},
            {"action": "remind", "text": content, "seconds": secs},
        ]
    # ---- 待办清单（完成 > 列出 > 新增）----
    if any(w in text for w in _TODO_DONE_WORDS):
        return [
            {"action": "show_bubble", "text": "好，这就替你划掉。"},
            {"action": "todo_done", "index": 1},
        ]
    if any(w in text for w in _TODO_LIST_WORDS):
        return [
            {"action": "show_bubble", "text": "我看看待办清单……"},
            {"action": "todo_list"},
        ]
    if any(w in text for w in _TODO_ADD_WORDS):
        content = text
        for w in _TODO_ADD_WORDS:
            content = content.replace(w, " ")
        content = content.strip(" 　，。！？、的：:")
        return [
            {"action": "show_bubble", "text": f"已记入待办：{content or text}"},
            {"action": "todo_add", "text": content or text},
        ]
    # ---- 长期记忆 ----
    if text.startswith("记住") or "帮我记住" in text \
            or "请记住" in text:
        fact = text.replace("帮我记住", " ").replace("请记住", " ")
        fact = fact.replace("记住", " ", 1).strip(" 　，。！？、：:")
        fact = f"用户说：{fact}" if fact else text
        return [
            {"action": "show_bubble", "text": f"记住了：{fact}"},
            {"action": "remember", "fact": fact},
        ]
    # ---- 工作日志（数字员工汇报）----
    if any(w in text for w in ("工作日志", "干了啥", "干了什么",
                               "做了什么", "汇报一下工作")):
        return [
            {"action": "show_bubble", "text": "我翻翻今天的工作记录……"},
            {"action": "work_log"},
        ]
    # ---- 联网搜索（需要实时信息/调研素材）----
    if any(w in text for w in ("搜一下", "搜索一下", "查一下", "查查",
                               "调研", "最新消息", "联网搜", "上网查")):
        query = text
        for w in ("帮我", "搜一下", "搜索一下", "查一下", "查查", "调研一下",
                  "调研", "联网搜", "上网查", "最新消息"):
            query = query.replace(w, " ")
        query = query.strip(" 　，。！？、：:吗呢吧") or text
        return [
            {"action": "show_bubble",
             "text": f"遵命，这就去联网查「{query}」{tail}"},
            {"action": "web_search", "query": query},
        ]

    if any(w in text for w in _OPEN_WORDS):
        app = _extract_app_name(text)
        return [
            {"action": "show_bubble", "text": f"遵命，这就打开{app}{tail}"},
            {"action": "move_to", "anchor": "taskbar"},
            {"action": "open_app", "app": app},
            {"action": "jump"},
        ]
    if any(w in text for w in _FIND_WORDS) and len(text) > 1:
        app = _extract_app_name(text)
        return [
            {"action": "show_bubble", "text": f"这就去找{app}。"},
            {"action": "search_app", "app": app},
        ]
    # ---- 文档生成（离线模式给演示内容，联网后由 AI 拟全稿）----
    # 样式/保存位置跟随用户原话（"标题红色""保存到桌面"）
    _doc_extra = {"style": _extract_style_hints(text),
                  "location": _extract_location(text)}
    low = text.lower()
    if "ppt" in low or "幻灯片" in text or "演示文稿" in text:
        return [
            {"action": "show_bubble",
             "text": f"遵命，一份演示文稿马上就好{tail}（离线示例内容）"},
            {"action": "create_doc", "doc_type": "ppt",
             "filename": "演示文稿.pptx",
             **{k: v for k, v in _doc_extra.items() if v},
             "content": {"title": "演示主题（离线示例）", "subtitle": "由凌零生成",
                         "template": "翡翠绿",
                         "slides": [
                             {"title": "背景与现状",
                              "bullets": ["行业规模近三年年均增长 15%，窗口期正在收窄",
                                          "现有方案集中在头部客户，中小企业市场仍是空白",
                                          "用户对一站式服务的需求同比提升 40%"]},
                             {"title": "核心数据一览",
                              "table": {"headers": ["指标", "去年同期", "本期", "变化"],
                                        "rows": [["营收(万元)", 860, 1120, "+30%"],
                                                 ["活跃客户", 2400, 3350, "+40%"],
                                                 ["客单价(元)", 3500, 3900, "+11%"]]}},
                             {"title": "问题与挑战",
                              "bullets": ["获客成本上升 22%，投放渠道亟待优化",
                                          "交付周期偏长，客户满意度承压",
                                          "竞品加速降价，利润空间被挤压"]},
                             {"title": "实施计划",
                              "bullets": ["第一阶段：完成渠道下沉与代理商体系建设",
                                          "第二阶段：上线标准化产品包，压缩交付周期至 2 周",
                                          "第三阶段：建立客户成功团队，提升续费与转介绍"]},
                             {"title": "资源与排期",
                              "table": {"headers": ["事项", "负责人", "时间节点"],
                                        "rows": [["渠道招募", "市场部", "10 月底"],
                                                 ["产品包上线", "产品部", "11 月中"],
                                                 ["客户成功启动", "服务部", "12 月初"]]}},
                             {"title": "预期成效",
                              "bullets": ["全年营收有望突破 1500 万，同比增长 35% 以上",
                                          "交付周期缩短一半，客户满意度回升至 95%",
                                          "形成可复制的区域打法，为明年扩张打底"]},
                         ]}},
        ]
    if "html" in low or "网页" in text:
        return [
            {"action": "show_bubble",
             "text": f"遵命，网页报告这就做{tail}（离线示例内容）"},
            {"action": "create_doc", "doc_type": "html",
             "filename": "网页报告.html",
             **{k: v for k, v in _doc_extra.items() if v},
             "content": {"title": "调研报告（离线示例）",
                         "subtitle": "凌零 · 桌面AI助手 出品",
                         "sections": [
                             {"heading": "背景",
                              "paragraphs": ["这是离线模式生成的示例网页报告，",
                                             "联网后凌零会先检索真实资料再动笔。"]},
                             {"heading": "关键数据",
                              "paragraphs": ["行业规模年均增长 15%",
                                             "中小企业渗透率不足 20%"],
                              "table": {"headers": ["指标", "数值"],
                                        "rows": [["年均增速", "15%"],
                                                 ["渗透率", "20%"]]}},
                         ]}},
        ]
    if "excel" in low or "表格" in text or "名单" in text or "报表" in text:
        return [
            {"action": "show_bubble",
             "text": f"遵命，表格这就来{tail}（离线示例内容）"},
            {"action": "create_doc", "doc_type": "xlsx",
             "filename": "数据表.xlsx",
             **{k: v for k, v in _doc_extra.items() if v},
             "content": {"sheets": [{"name": "示例数据",
                                     "headers": ["项目", "负责人", "进度"],
                                     "rows": [["需求梳理", "凌零", "100%"],
                                              ["方案设计", "凌零", "60%"],
                                              ["测试验收", "凌零", "0%"]]}]}},
        ]
    if "word" in low or "文档" in text or "报告" in text or "文章" in text:
        return [
            {"action": "show_bubble",
             "text": f"遵命，文档这就拟好{tail}（离线示例内容）"},
            {"action": "create_doc", "doc_type": "doc",
             "filename": "文档.docx",
             **{k: v for k, v in _doc_extra.items() if v},
             "content": {"title": "项目简报（离线示例）",
                         "sections": [
                             {"heading": "背景",
                              "paragraphs": ["这是离线模式生成的示例文档，"
                                             "联网后凌零可以按你的要求写全稿。"]},
                             {"heading": "结论",
                              "paragraphs": ["助手与本地工具结合，效率翻倍。",
                                             "后续将接入更多系统工具，把"
                                             "「吩咐一声就办」覆盖到更多场景。"]},
                         ]}},
        ]

    if any(w in text for w in _WEATHER_WORDS):
        return [
            {"action": "show_bubble", "text": "我看看天气预报..."},
            {"action": "show_card", "title": "广州 天气",
             "path": "晴 26°C ~ 33°C | 适合穿短袖，注意防晒",
             "size": "空气质量: 良"},
        ]

    # ---- 多步任务规划（放文档分支后：避免劫持"帮我做个PPT"）----
    # 只出计划等用户在卡片上确认，绝不自动开始执行
    if any(w in text for w in _PLAN_WORDS):
        return [
            {"action": "show_bubble",
             "text": f"遵命，我先拟个计划{tail}。你在计划卡上点「开始执行」"
                     f"我就动手；要调整直接说。"},
            {"action": "show_plan", "steps": [
                "明确需求与目标",
                "收集整理素材",
                "产出初稿/成果",
                "检查并交付",
            ]},
        ]

    # 其他所有话题：通用回复（离线降级版）
    return [{"action": "show_bubble", "text": _mock_answer(text, personality)}]
