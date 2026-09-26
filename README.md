# 地道句子本

一个 Mac 桌面软件：每天把想说的中文变成地道英文、弄懂看不懂的英文、检查自己写的英文。问过的每一句都会自动存进「句子本」，左边中文、右边英文，可以遮住一边、像背单词一样翻卡片复习。

## 怎么用

1. 打开 `dist/地道句子本.app`（可以拖进「应用程序」文件夹）。
2. 第一次用，去「设置」里粘贴 Anthropic API key。它会先验证，再存进 Mac 的钥匙串。
   - key 在 [console.anthropic.com](https://console.anthropic.com) 的 API Keys 页面创建，Billing 页面充值。
   - API 按用量付费，和 Claude 会员订阅分开计费。查一句话一般几美分，「设置」里能看到今天和本月的花费估算。
3. 在「对话」里直接发中文或英文：
   - 发中文：给你 2 到 4 个不同语气的地道说法、值得记住的表达和常见错误。
   - 发英文：翻译成中文，用颜色标出句子成分，讲清楚这句话怎么理解。
   - 发自己写的英文（可以加一句“这样说对吗”）：判断对不对、地不地道，标出改了哪里，并逐条解释。
   - 也可以直接提问、追问，或者问“我之前问过的那句……怎么说来着”。
4. 「句子本」：遮住中文或英文，点一下单独翻开一句；可以筛选、搜索、打乱顺序、导出 CSV。
5. 「复习」：翻卡片。选「记住了」，这句隔 1、3、7、16、35 天再出现；选「还不熟」，这一轮结束前会再考你一次。

快捷键：回车发送，Shift + 回车换行，⌘N 新对话；复习时空格翻面，1 还不熟，2 记住了。

## 它是怎么搭的

```
窗口（pywebview，系统自带的 WebKit）
  └─ 界面：src/sentence_agent/static（原生 JS，不需要打包工具）
        │  HTTP + 流式事件（只监听 127.0.0.1，每次启动生成新的访问口令）
本地服务（FastAPI）            src/sentence_agent/server.py
  ├─ Agent                    src/sentence_agent/agent/
  │    ├─ session.py   Anthropic 官方 SDK 的 tool runner：流式输出、自动调用工具、对话历史原样存档
  │    ├─ tools.py     6 个工具：存译句 / 存解析 / 存纠错 / 查句子本 / 记易错点 / 看学习概况
  │    ├─ prompts.py   系统提示词 + 学习者档案（易错点，做成长期记忆）
  │    └─ transcript.py  把存档的 API 历史还原成聊天界面
  ├─ SQLite 数据库              src/sentence_agent/store.py
  ├─ 间隔重复算法               src/sentence_agent/srs.py
  └─ API key：macOS 钥匙串      src/sentence_agent/credentials.py
```

几个设计点：

- **Agent 自己判断你想做什么**：它根据你发的内容决定调用哪个工具。三个「存」工具的参数就是卡片内容，一边生成一边显示在界面上，生成完就进了句子本。
- **对话能接着聊**：每一轮的完整 API 历史（包括思考签名、工具调用和结果）都原样存进数据库，重开软件也能接着问。如果中途停止或者软件被关掉，下一次发消息前会自动补齐未完成的工具调用。
- **长期记忆**：检查英文时发现的反复错误会被记成易错点，新对话开始时带进系统提示词。你可以在「设置」里查看和删除。
- **省钱**：系统提示词和对话历史都用了 prompt caching；默认模型是 Claude Opus 5，思考深度默认「均衡」，都可以在设置里改。选 Opus 5 或 Fable 5.1 时会开启服务端 fallback，模型拒答时自动换备用模型接手。
- **数据在本机**：`~/Library/Application Support/SentenceAgent/sentence_agent.db`。

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest              # 测试：用模拟的 Claude API 跑真实的 agent 循环，不花钱
.venv/bin/python -m sentence_agent      # 从源码启动桌面窗口
.venv/bin/python scripts/dev_server.py  # 开发服务器：假的 Claude + 演示数据，在浏览器打开 http://127.0.0.1:8765/#t=dev
scripts/build_app.sh                    # 重新打包 dist/地道句子本.app
```

这个 .app 是在本机打包的，没有做 Apple 开发者签名。在这台 Mac 上可以直接打开；拷到别的 Mac 上，第一次需要右键选「打开」。
