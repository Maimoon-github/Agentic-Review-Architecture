"""
Management command: run_pipeline

Starts a pipeline synchronously (Celery eager mode) and prints
the final output to stdout.

Usage:
    python manage.py run_pipeline "Write a blog post about AI agents"
    python manage.py run_pipeline "Design a REST API" --max-iterations 5
"""

import time
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from orchestration.models import PipelineRun
from orchestration.tasks import run_orchestrator


class Command(BaseCommand):
    help = 'Run an orchestration pipeline synchronously (eager Celery mode).'

    def add_arguments(self, parser):
        parser.add_argument(
            'task_description',
            type=str,
            help='The task description to pass to the pipeline.',
        )
        parser.add_argument(
            '--max-iterations',
            type=int,
            default=7,
            dest='max_iterations',
            help='Maximum number of Reviewer→re-plan iterations (default: 7).',
        )
        parser.add_argument(
            '--poll-interval',
            type=float,
            default=2.0,
            dest='poll_interval',
            help='Seconds between DB polls when using real Celery (default: 2).',
        )

    def handle(self, *args, **options):
        task_description = options['task_description'].strip()
        max_iterations = options['max_iterations']
        poll_interval = options['poll_interval']

        self.stdout.write(self.style.MIGRATE_HEADING('═' * 60))
        self.stdout.write(self.style.MIGRATE_HEADING('  Multi-Agent Orchestration Pipeline'))
        self.stdout.write(self.style.MIGRATE_HEADING('═' * 60))
        self.stdout.write(f'\nTask: {task_description}\n')

        # Create PipelineRun
        pipeline = PipelineRun.objects.create(
            task_description=task_description,
            max_iterations=max_iterations,
            status=PipelineRun.Status.PENDING,
        )
        self.stdout.write(f'Pipeline ID: {pipeline.id}\n')

        # Run in EAGER mode so all Celery tasks execute synchronously in-process.
        eager_settings = {
            'CELERY_TASK_ALWAYS_EAGER': True,
            'CELERY_TASK_EAGER_PROPAGATES': True,
        }

        self.stdout.write(self.style.WARNING('\nRunning pipeline (eager mode)…\n'))
        try:
            with override_settings(**eager_settings):
                run_orchestrator.delay(str(pipeline.id))
        except Exception as exc:
            raise CommandError(f'Pipeline execution error: {exc}') from exc

        # After eager execution the pipeline should be terminal; refresh from DB.
        pipeline.refresh_from_db()

        terminal_statuses = {
            PipelineRun.Status.DONE,
            PipelineRun.Status.FAILED,
            PipelineRun.Status.MAX_ITER,
        }

        # In non-eager environments poll until terminal.
        if pipeline.status not in terminal_statuses:
            self.stdout.write('Polling for completion…')
            while pipeline.status not in terminal_statuses:
                time.sleep(poll_interval)
                pipeline.refresh_from_db()
                self.stdout.write(
                    f'  status={pipeline.status}  iteration={pipeline.iteration_count}',
                    ending='\r',
                )
            self.stdout.write('')  # newline after \r loop

        self.stdout.write('\n' + '─' * 60)
        self.stdout.write(f'Status         : {pipeline.status}')
        self.stdout.write(f'Iterations used: {pipeline.iteration_count} / {pipeline.max_iterations}')
        self.stdout.write('─' * 60)

        if pipeline.status == PipelineRun.Status.DONE:
            self.stdout.write(self.style.SUCCESS('\n✓ Pipeline completed successfully.\n'))
            self.stdout.write(self.style.MIGRATE_HEADING('FINAL OUTPUT:'))
            self.stdout.write('─' * 60)
            self.stdout.write(pipeline.final_output)
            self.stdout.write('─' * 60 + '\n')
        elif pipeline.status == PipelineRun.Status.MAX_ITER:
            self.stdout.write(self.style.WARNING(
                f'\n⚠ Pipeline stopped: maximum iterations ({pipeline.max_iterations}) reached.'
            ))
        else:
            raise CommandError('Pipeline ended with status FAILED. Check AgentLog for details.')
