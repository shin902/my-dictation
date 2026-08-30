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

- ITNはNFKC数字、および日付・時刻・金額・明示した単位の直前にある漢数字だけを扱います。電話番号、住所、曖昧な助数詞は対象外です。
- Mondegreen connectorは設定したcanonical termと発音aliasだけを厳しい閾値で局所置換します。LM rerankerや辞書自動更新はありません。
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
- [x] 数字・日付・時刻・金額・単位に限定した決定的ITNとchanges
- [x] 小さな手動辞書によるMondegreen接続、厳しい照合、保護語
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
