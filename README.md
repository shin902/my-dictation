# my-dictation

Groqの1-best ASRを、限定的な日本語ITN、手動用語辞書（Mondegreen）、OpenAI互換LLMによる保守的校正へ通す、小さく監査可能なPython CLIです。各入力を1 JSONに保存します。

## 必要環境

- Python 3.11以上
- 音声認識にはGroq API key
- LLM校正には任意のOpenAI互換API（未設定でも動作可能）

## 使い方

### 1. セットアップ

```sh
git clone https://github.com/shin902/my-dictation.git
cd my-dictation
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp config.example.toml config.toml
```

GroqのAPI keyを設定します。

```sh
export GROQ_API_KEY='gsk_...'
```

LLM校正も使う場合は、OpenAI互換APIの接続情報を設定します。未設定でも、ASR・ITN・用語補正までは動作します。

```sh
export LLM_API_KEY='...'
export LLM_MODEL='gpt-4o-mini'
# OpenAI以外の互換serverを使う場合
export LLM_BASE_URL='http://localhost:11434/v1'
```

既定ではカレントディレクトリの`config.toml`を読みます。別の設定を使う場合は、各コマンドの前に`--config`を指定します。

```sh
my-dictation --config /path/to/config.toml process-text 'テストです'
```

### 2. まずテキストだけ試す

ASRを使わず、ITN・用語補正・LLM校正だけを確認できます。

```sh
my-dictation process-text '二千二十四年三月五日にクバネティスを使います'
```

最終テキストはstdout、作成した履歴JSONのpathはstderrへ出ます。

```text
2024-3-5にKubernetesを使います。
record: data/records/2026-08-31/123456-<uuid>.json
```

### 3. 音声ファイルを文字起こしする

```sh
my-dictation transcribe recording.wav
```

対応音声形式はGroq Speech-to-Text APIが受け付ける形式に従います。処理順は次の通りです。

```text
Groq ASR → ITN → Mondegreen用語補正 → LLM校正 → 出力
```

音声は送信前に`data/spool/`へ一時コピーされます。ASRと履歴保存が成功すると削除され、失敗した場合だけ残ります。

### 4. 失敗した音声を再試行する

spool内の全音声を再試行します。

```sh
my-dictation retry
```

ファイル名の一部を指定して、1件だけ再試行することもできます。

```sh
find data/spool -type f
my-dictation retry 'ファイル名またはIDの一部'
```

再試行に成功した音声はspoolから削除されます。

### 5. 用語辞書を設定する

`config.toml`の`[terminology]`へ、正規表記と認識されやすい読みを追加します。

```toml
[terminology]
"Kubernetes" = ["クバネティス", "クーベネティス"]
"Groq" = ["グロック"]
"ROCmFPX" = ["ロックムエフピーエックス"]
```

`config.toml`はGit管理対象外です。API keyは書かず、環境変数を使用してください。

### 6. 履歴と手動修正

履歴は1入力につき1ファイルです。

```text
data/records/YYYY-MM-DD/HHMMSS-<uuid>.json
```

JSONには`raw`、ITN・用語補正・LLM校正の各結果、最終`output`が入ります。結果が悪かった場合だけ、対象JSONの`manual_correction`へ修正文を手動で記入できます。

```json
{
  "output": "機械が出した文章",
  "manual_correction": "自分で直した文章"
}
```

### 7. 実際のMondegreen / WeTextProcessingを使う（任意）

基本インストールでは軽量な内蔵処理を使います。外部実装を使う場合だけ追加します。

```sh
pip install -e '.[external-processors]'
```

`config.toml`を変更します。

```toml
[processors]
itn = "wetextprocessing"
terminology = "mondegreen"
terminology_glossary = "config/glossary.csv"
```

`glossary.csv`の形式はNagaYu/mondegreenの仕様に従います。外部processorが未導入または失敗した場合は、内蔵処理へfallbackします。環境変数`MY_DICTATION_ITN_BACKEND`と`MY_DICTATION_TERMINOLOGY_BACKEND`でも切り替えられます。

### CLI一覧

```sh
my-dictation transcribe <audio-file>
my-dictation retry [record-or-audio-id]
my-dictation process-text <text>
my-dictation --help
```

最終テキストだけがstdoutに出ます。履歴pathとエラーはstderrです。

## 処理上の制約

- ITNは全角数字、および日付・時刻・金額・明示した単位を伴う数字だけを扱います。外部adapterでもspanを限定し、電話番号、住所、曖昧な助数詞は対象外です。
- 用語補正は、内蔵matcherまたは実際のNagaYu/mondegreen connectorを明示選択します。いずれもLM rerankerを使わず、辞書自動更新もしません。
- LLMには情報追加、要約、文体変更を禁止する構造化JSON promptを送り、保護語が1つでも消えた場合やAPI/JSONエラー時には用語補正後へfallbackします。
- N-best、学習、GUI、常駐録音、SQLiteは初期scope外です。

## Test

外部APIやkeyなしで実行できます。

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

## PLAN.md 自己監査チェックリスト

- [x] Groq ASR adapter（provider responseを隔離、1-best、失敗理由）
- [x] 送信前atomic spool、失敗時保持、明示retry
- [x] ASR成功・履歴保存完了後のみ音声削除
- [x] WeTextProcessing日本語APIの隔離adapter（optional extra、対象span限定、changes、内蔵fallback）
- [x] NagaYu/mondegreen `load_glossary` / `ConstrainedCorrector` のLMなし隔離adapter（optional extra、保護語、内蔵fallback）
- [x] vendor SDK非依存のOpenAI互換LLM、構造化出力、保守的prompt
- [x] 保護語検証、違反・timeout・API失敗時fallback
- [x] 各段階のinput/output/changeを含む1入力1 JSON
- [x] 一時file + fsync + renameによるatomic履歴保存
- [x] `manual_correction` のatomic手動記録
- [x] `transcribe` / `retry` / `process-text` CLIとstdout/stderr分離
- [x] TOML + 環境変数設定（base URL/key/model/timeout/temperature/dictionary）
- [x] key・音声を履歴JSONへ保存しない
- [x] mockだけで成功/失敗/順序/fallback/atomic性を検証するtests
- [x] 明示された初期scope外機能を追加しない
