FROM ecosyncbench/base/php-composer:8.4

COPY run_getsentry_logs_profile_inside.sh /opt/ecosync/run-getsentry-logs-profile.sh
RUN chmod 0755 /opt/ecosync/run-getsentry-logs-profile.sh

WORKDIR /workspace
