"""
Celery Tasks
"""

import logging
import os
import time
from collections import namedtuple
from typing import TYPE_CHECKING

from .worker import celery
from .config import BOT_NAME, BOT_EMAIL
from .. import utils
from ..recipe import Recipe
from ..githandler import TempGitHandler

if TYPE_CHECKING:
    from .worker import AsyncTask
    from typing import Dict


logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


PRInfo = namedtuple('PRInfo', 'installation user repo ref recipes issue_number')



class PrBranch:
    def __init__(self, ghappapi, pr_info):
        self.ghappapi = ghappapi
        self.pr_info = pr_info
        self.cwd = None
        self.git = None

    async def __aenter__(self):
        logger.error("Prepping branch %s", self.pr_info)
        token = await self.ghappapi.get_installation_token(self.pr_info.installation)
        self.git = TempGitHandler(password=token,
                                  fork=self.pr_info.user + "/" + self.pr_info.repo)
        self.git.set_user(BOT_NAME, BOT_EMAIL)

        self.cwd = os.getcwd()
        os.chdir(self.git.tempdir.name)

        branch = self.git.create_local_branch(self.pr_info.ref)
        branch.checkout()
        return self.git

    async def __aexit__(self, exc_type, exc, tb):
        os.chdir(self.cwd)
        self.git.close()


@celery.task(bind=True, acks_late=True)
async def bump(self: "AsyncTask", pr_info_dict: "Dict", ghapi_data=None):
    """Bump the build number in each recipe"""
    pr_info = PRInfo(**pr_info_dict)
    logger.info("Processing bump command: %s", pr_info)
    async with PrBranch(self.ghappapi, pr_info) as git:
        for meta_fn in pr_info.recipes:
            recipe = Recipe.from_file('recipes', meta_fn)
            buildno = int(recipe.meta['build']['number']) + 1
            recipe.reset_buildnumber(buildno)
            recipe.save()
        msg = f"Bump {recipe} buildno to {buildno}"
        if not git.commit_and_push_changes(pr_info.recipes, pr_info.ref, msg, sign=True):
            logger.error("Failed to push?!")


@celery.task(bind=True, acks_late=True)
async def lint(self: "AsyncTask", pr_info_dict: "Dict", ghapi_data=None):
    """Lint each recipe"""
    pr_info = PRInfo(**pr_info_dict)
    logger.info("Processing lint command: %s", pr_info)

    async with PrBranch(self.ghappapi, pr_info) as git:
        utils.load_config('config.yml')
        from bioconda_utils.linting import lint as _lint, LintArgs, markdown_report
        recipes = [r[:-len('/meta.yaml')] for r in pr_info.recipes]
        df = _lint(recipes, LintArgs())
        msg = markdown_report(df)
        await self.ghapi.create_comment(pr_info.issue_number, msg)


@celery.task(acks_late=True)
def sleep(seconds, msg):
    """Demo task that just sleeps for a given number of seconds"""
    logger.info("Sleeping for %i seconds: %s", seconds, msg)
    for second in range(seconds):
        time.sleep(1)
        logger.info("Slept for %i seconds: %s", second, msg)
    logger.info("Waking: %s", msg)