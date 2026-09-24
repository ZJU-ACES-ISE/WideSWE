Add Cohere ASR support across Transformers and Transformers.js.

Cohere ASR is a 2B parameter Conformer-based encoder-decoder speech recognition model. Add support for Cohere ASR so it can be loaded and run through the Transformers APIs and through the Transformers.js runtime.

Support short-form transcription. Pass `punctuation=False` to obtain lower-cased output without punctuation marks. For audio longer than the feature extractor's `max_audio_clip_s`, the feature extractor automatically splits the waveform into chunks, and the processor reassembles the per-chunk transcriptions using the returned `audio_chunk_index`. Multiple audio files can be processed in a single call, including batches that mix short-form and long-form audio. Specify the language code to transcribe in any of the 14 supported languages.

Expose the expected `cohere_asr` model type and Cohere ASR API surface, including `CohereAsrConfig`, `CohereAsrFeatureExtractor`, `CohereAsrProcessor`, `CohereAsrModel`, and `CohereAsrForConditionalGeneration`, so the model works through `AutoProcessor`, auto model mappings, and automatic-speech-recognition pipelines.

Expose `split_audio()` on `CohereAsrFeatureExtractor`. Audio at or below `max_audio_clip_s` should remain one chunk; longer audio should be split into chunks that together cover the full waveform. The default `max_audio_clip_s` is 35 seconds.

The JavaScript runtime should match the Python reference behavior for Cohere ASR feature extraction, model behavior, and automatic-speech-recognition pipeline compatibility.
