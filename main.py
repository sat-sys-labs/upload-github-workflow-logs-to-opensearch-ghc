import os
import time
import requests
import io
import logging
import sys
import signal
import json
# don't remove, it loads the configuration
import logger


def main():
    # User provided variables
    github_repo = os.environ.get("INPUT_GITHUB_REPOSITORY")
    try:
        assert github_repo not in (None, '')
    except:
        output = "The input github repository is not set"
        print(f"Error: {output}")
        sys.exit(-1)

    github_run_id = os.environ.get("INPUT_GITHUB_RUN_ID")
    try:
        assert github_run_id not in (None, '')
    except:
        output = "The input github run id is not set"
        print(f"Error: {output}")
        sys.exit(-1)

    github_token = os.environ.get("INPUT_GITHUB_TOKEN")
    try:
        assert github_token not in (None, '')
    except:
        output = "The input github token is not set"
        print(f"Error: {output}")
        sys.exit(-1)

    github_org = os.environ.get("INPUT_GITHUB_ORG")
    try:
        assert github_org not in (None, '')
    except:
        output = "The input github org is not set"
        print(f"Error: {output}")
        sys.exit(-1)
    github_host_api = os.environ.get("INPUT_GITHUB_HOST_API")
    try:
        assert github_host_api not in (None, '')
    except:
        output = "The input github host api is not set"
        print(f"Error: {output}")
        sys.exit(-1)        
    elastic_logger = logging.getLogger("elastic")
    # Build jobs_url directly - avoids the /actions/runs/{id} endpoint which
    # fails on GHE with "suffixed values" error
    jobs_url = f"{github_host_api}/repos/{github_org}/{github_repo}/actions/runs/{github_run_id}/jobs"
    metadata = {}

    # extract all done jobs
    jobs = {}
    try:
        jobs_response = requests.get(jobs_url, headers={
            "Authorization": f"token {github_token}"
        })
        if not jobs_response.ok:
            raise Exception(f"Failed to get run jobs: GitHub API returned {jobs_response.status_code} for {jobs_url}")
        _jobs = json.loads(jobs_response.content)
        # Extract run-level metadata from the first job entry
        first_job = _jobs.get('jobs', [{}])[0] if _jobs.get('jobs') else {}
        metadata = {
            "metadata_workflow_name": first_job.get('workflow_name'),
            "metadata_head_branch": first_job.get('head_branch'),
            "metadata_head_sha": first_job.get('head_sha'),
            "metadata_run_attempt": first_job.get('run_attempt'),
            "metadata_run_id": github_run_id,
            "metadata_repository": f"{github_org}/{github_repo}",
        }
        for job in _jobs.get('jobs'):
            job_id = job.get('id')
            # no logs for jobs that weren't completed
            if not job.get('status') == 'completed':
                continue
            jobs[job_id] = {
                "job_id": job_id,
                "job_name": job.get('name'),
                "job_status": job.get('status'),
                "job_conclusion": job.get('conclusion'),
                "job_steps": job.get('steps')
            }
            # log this metadata to elastic
            elastic_logger.info("Job metadata", extra={
                **jobs.get(job_id)
            })
    except Exception as exc:
        output = "Failed to get run jobs" + str(exc)
        print(f"Error: {output}")
        print(f"::set-output name=result::{output}")
        sys.exit(-1)

    for job_id in jobs:
        try:
            job_logs_url = f"{github_host_api}/repos/{github_org}/{github_repo}/actions/jobs/{job_id}/logs"
            MAX_TRIES = 5
            SLEEP_SECS = 2
            r = None
            for attempt in range(1, MAX_TRIES + 1):
                r = requests.get(job_logs_url, stream=True, headers={
                    "Authorization": f"token {github_token}"
                })
                if r.status_code in (500, 502, 503, 504, 404):
                    if attempt < MAX_TRIES:
                        time.sleep(SLEEP_SECS * attempt)  # simple backoff
                        continue
                    print(f"Warning: status {r.status_code} for job {job_id}, skipping logs for this job")
                    r = None
                    break
                if not r.ok:
                    output = "Failed to download logs"
                    print(f"Error: {output}")
                    print(f"::set-output name=result::{output}")
                    sys.exit(-1)
                break
            if r is None:
                continue

            logs = io.BytesIO(r.content)
            for log in logs:
                elastic_logger.info(log.strip().decode(), extra={
                    "job_id": job_id,
                    "job_name": jobs.get(job_id).get('job_name'),
                    "repo": github_repo,
                    "run_id": github_run_id,
                    **metadata
                })

        except requests.exceptions.HTTPError as errh:
            output = "GITHUB API Http Error:" + str(errh)
            print(f"Error: {output}")
            print(f"::set-output name=result::{output}")
            sys.exit(-1)
        except requests.exceptions.ConnectionError as errc:
            output = "GITHUB API Error Connecting:" + str(errc)
            print(f"Error: {output}")
            print(f"::set-output name=result::{output}")
            sys.exit(-1)
        except requests.exceptions.Timeout as errt:
            output = "Timeout Error:" + str(errt)
            print(f"Error: {output}")
            print(f"::set-output name=result::{output}")
            sys.exit(-1)
        except requests.exceptions.RequestException as err:
            output = "GITHUB API Non catched error connecting:" + str(err)
            print(f"Error: {output}")
            print(f"::set-output name=result::{output}")
            sys.exit(-1)


def keyboard_interrupt_bug(signal, frame):
    print('keyboard interrupt')
    pass


signal.signal(signal.SIGINT, keyboard_interrupt_bug)


if __name__ == "__main__":
    main()