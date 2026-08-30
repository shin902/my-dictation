# my-dictation

Groqの1-best ASRを、限定的な日本語ITN、手動用語辞書（Mondegreen）、OpenAI互換LLMによる保守的校正へ通す、小さく監査可能なPython CLIです。各入力を1 JSONに保存します。

## Requirements / install

Python 3.11以上。

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp config.example.toml config.toml
```

APIキーは設定ファイルに書かず環境変数で渡してください。

```sh
export GROQ_API_KEY=...
export LLM_API_KEY=...       # LLM校正を使う場合のみ
export LLM_MODEL=gpt-4o-mini # または config.toml の api.llm_model
```

`LLM_API_KEY`またはmodelがない場合、LLM段階は安全に不採用となり、用語補正後の文を返します。設定ファイルは `--config` または `MY_DICTATION_CONFIG` で選べます。base URL、model、timeout、temperature、用語辞書の例は `config.example.toml` を参照してください。GroqとLLMはいずれもOpenAI互換HTTP endpointへ標準ライブラリだけで接続します。

基本installは軽量な内蔵processorを使います。実プロジェクトのadapterを選ぶ場合だけ、必要なextraをinstallし、`[processors]` で明示的に切り替えます。

```sh
pip install -e '.[wetextprocessing]' # WeTextProcessing（Pyniniを含む）
pip install -e '.[mondegreen]'       # NagaYu/mondegreen（G2P依存を含む）
# または: pip install -e '.[external-processors]'
```

```toml
[processors]
itn = "wetextprocessing"       # default: "builtin"
terminology = "mondegreen"     # default: "builtin"
terminology_glossary = "config/glossary.csv"
```

WeText adapterは `tn.japanese.normalizer.Normalizer` に対象カテゴリのspanだけを渡します。Mondegreen adapterは `load_glossary` と `ConstrainedCorrector(..., use_lm=false)` を使います。外部moduleのimport・実行に失敗した場合は安全に内蔵processorへfallbackし、base installの従来動作を維持します。backend名が不正、またはMondegreen選択時にglossary pathがない設定は起動時エラーになります。環境変数 `MY_DICTATION_ITN_BACKEND` / `MY_DICTATION_TERMINOLOGY_BACKEND` でもbackendを選択できます。

## CLI

```sh
my-dictation transcribe recording.wav
my-dictation retry                 # spool内の全件
my-dictation retry '<id-fragment>' # 一致する音声だけ
my-dictation process-text '三個のクバネティス'
```

最終テキストだけがstdoutに出ます。履歴pathとエラーはstderrです。ASR前に音声を `data/spool` へatomicにコピーし、ASR成功かつ履歴のatomic保存完了後だけ削除します。失敗音声は `retry` まで残ります。音声を恒久保存する設計ではないため、成功時には削除されます。

履歴は `data/records/YYYY-MM-DD/HHMMSS-<uuid>.json` に各段階の入出力、変更、保護語、採否を保存します。API keyと音声は保存しません。悪い結果の手動訂正は、ライブラリの `RecordStore.correct(Path(record), "修正文")` で同じJSONの `manual_correction` にatomicに記録できます。

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
