"""Prometheus metrics. Never label with patent content, file names or request ids."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

REQUESTS = Counter("patent_requests_total", "Requests by endpoint and outcome", ["endpoint", "outcome"], registry=REGISTRY)
FAILURES = Counter("patent_failures_total", "Failures by error code", ["code"], registry=REGISTRY)
DURATION = Histogram("patent_processing_seconds", "End-to-end processing duration", ["endpoint"],
                     buckets=(1, 5, 15, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600), registry=REGISTRY)
PHASE_DURATION = Histogram("patent_phase_seconds", "Duration of processing phases", ["phase"],
                           buckets=(0.01, 0.1, 0.5, 1, 5, 15, 60, 300, 900), registry=REGISTRY)
INPUT_BYTES = Histogram("patent_input_bytes", "Uploaded DOCX size", ["language"],
                        buckets=[x * 1024 * 1024 for x in (1, 5, 10, 25, 50, 75, 100, 150, 200)], registry=REGISTRY)
OUTPUT_BYTES = Histogram("patent_output_bytes", "Response body size",
                         buckets=[x * 1024 * 1024 for x in (1, 5, 10, 25, 50, 100, 200)], registry=REGISTRY)
CLAIMS = Histogram("patent_claims_detected", "Claims detected per document", ["language"],
                   buckets=(1, 10, 25, 50, 100, 200, 300, 500, 1000), registry=REGISTRY)
IMAGES = Counter("patent_images_total", "Images seen in documents", ["language"], registry=REGISTRY)
EQUATIONS = Counter("patent_equations_total", "OMML equations seen", ["language"], registry=REGISTRY)
UNSUPPORTED = Counter("patent_unsupported_objects_total", "Risk findings by code", ["code"], registry=REGISTRY)
ACTIVE_JOBS = Gauge("patent_active_jobs", "Jobs currently running in this worker", registry=REGISTRY)
PEAK_RSS = Gauge("patent_last_job_peak_rss_bytes", "Peak RSS of the most recent job", registry=REGISTRY)
PEAK_CPU = Gauge("patent_last_job_peak_cpu_percent", "Peak CPU of the most recent job", registry=REGISTRY)
TEMP_FREE = Gauge("patent_temp_storage_free_bytes", "Free bytes on the temporary storage volume", registry=REGISTRY)
TEMP_USED = Gauge("patent_temp_storage_used_bytes", "Bytes used by job workspaces", registry=REGISTRY)
