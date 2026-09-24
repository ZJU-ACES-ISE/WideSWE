Propagate filtered Hugging Face Hub request headers into hf_xet CAS requests.

The user-agent header created by `huggingface_hub` needs to be present in CAS requests. Add an optional `request_headers` map to the hf_xet `upload_files`, `upload_bytes`, and `download_files` API calls so filtered headers can be passed along with requests to xetcas.

Pass along the headers without the Authorization header. Augment the request header map with the hf_xet user agent string, appending it to an existing user-agent value or setting it when one is not provided.
