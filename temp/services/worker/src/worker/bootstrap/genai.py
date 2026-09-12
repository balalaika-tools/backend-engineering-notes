"""Composition of worker-owned GenAI models, agents, tools, and adapters."""

from contextvars import ContextVar
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from worker.config.settings import Settings
from worker.domain.admission import WindowCounters
from worker.genai.config_summarization.llm import build_model as build_config_summary_model
from worker.genai.config_summarization.summarizer import (
    ChatModelConfigSummaryGenerator,
    LLMConfigSummarizer,
)
from worker.genai.exception_analysis.agent import build_agent as build_analysis_agent
from worker.genai.exception_analysis.contextual_analyst import (
    AnalysisHarnessFactory,
    ContextualLLMExceptionAnalyst,
)
from worker.genai.exception_analysis.harness import LangChainAnalysisHarness
from worker.genai.exception_analysis.llm import build_model as build_analysis_model
from worker.genai.exception_analysis.llm import build_summarizer_model
from worker.genai.exception_analysis.middleware import build_middleware
from worker.genai.exception_analysis.schemas import AnalysisOutput
from worker.genai.exception_analysis.tools import build_tools
from worker.genai.shared.llm_client import (
    LLMRetryPolicy,
    RetryingLLMClient,
    classify_provider_error,
)
from worker.genai.sql_fixer.agent import build_agent as build_sql_fixer_agent
from worker.genai.sql_fixer.fixer import SQLFixerAgent
from worker.genai.sql_fixer.llm import build_model as build_sql_fixer_model
from worker.genai.sql_fixer.prompts import build_system_prompt as build_sql_fixer_prompt
from worker.genai.sql_fixer.tools import build_execute_sql_tool
from worker.observability.genai import OTelModelCallback, trace_tool_call
from worker.ports.control_context.config_summarizer import ConfigSummarizer
from worker.ports.investigation.exception_analyst import ExceptionAnalyst
from worker.ports.investigation.query_executor import QueryExecutor


@dataclass(frozen=True, slots=True)
class GenAIComponents:
    summarizer: ConfigSummarizer
    analyst: ExceptionAnalyst


def build_genai_components(
    *,
    settings: Settings,
    counters: WindowCounters,
    query_executor: QueryExecutor,
) -> GenAIComponents:
    retry_client = RetryingLLMClient(
        policy=LLMRetryPolicy(
            max_attempts=settings.llm_max_attempts,
            backoff_base_seconds=settings.llm_backoff_base_seconds,
            backoff_cap_seconds=settings.llm_backoff_cap_seconds,
        ),
        counters=counters,
        classify_error=classify_provider_error,
    )
    callback = OTelModelCallback()
    config_model, analysis_model, summarizer_model, sql_fixer_model = _build_models(
        settings, callback
    )
    harness_factory = _build_harness_factory(
        settings=settings,
        analysis_model=analysis_model,
        summarizer_model=summarizer_model,
        sql_fixer_model=sql_fixer_model,
        query_executor=query_executor,
        retry_client=retry_client,
    )
    return GenAIComponents(
        summarizer=LLMConfigSummarizer(
            ChatModelConfigSummaryGenerator(model=config_model, retry_client=retry_client)
        ),
        analyst=ContextualLLMExceptionAnalyst(
            harness_factory=harness_factory,
            record_comment_limit=settings.record_comment_limit,
            tool_call_limit=settings.analysis_tool_call_limit,
        ),
    )


def _build_models(
    settings: Settings,
    callback: OTelModelCallback,
) -> tuple[BaseChatModel, BaseChatModel, BaseChatModel, BaseChatModel]:
    callbacks = [callback]
    return (
        build_config_summary_model(
            model_id=settings.summarizer_model_id,
            region_name=settings.model_region,
            callbacks=callbacks,
        ),
        build_analysis_model(
            model_id=settings.analysis_model_id,
            region_name=settings.model_region,
            callbacks=callbacks,
        ),
        build_summarizer_model(
            model_id=settings.summarizer_model_id,
            region_name=settings.model_region,
            callbacks=callbacks,
        ),
        build_sql_fixer_model(
            model_id=settings.sql_fixer_model_id,
            region_name=settings.model_region,
            callbacks=callbacks,
        ),
    )


def _build_harness_factory(
    *,
    settings: Settings,
    analysis_model: BaseChatModel,
    summarizer_model: BaseChatModel,
    sql_fixer_model: BaseChatModel,
    query_executor: QueryExecutor,
    retry_client: RetryingLLMClient,
) -> AnalysisHarnessFactory:
    def build_harness(
        *,
        system_prompt: str,
        output_schema: type[AnalysisOutput],
        schema_context: str,
    ) -> LangChainAnalysisHarness:
        successful_results: ContextVar[list[str] | None] = ContextVar(
            "sql_fixer_successful_results",
            default=None,
        )
        sql_tool = build_execute_sql_tool(
            query_executor=query_executor,
            successful_results=successful_results,
        )
        sql_graph = build_sql_fixer_agent(
            model=sql_fixer_model,
            tools=(sql_tool,),
            system_prompt=build_sql_fixer_prompt(schema_context),
            retry_client=retry_client,
            max_attempts=settings.sql_fixer_max_attempts,
        )
        sql_fixer = SQLFixerAgent(sql_graph, successful_results)
        tools = build_tools(query_executor=query_executor, sql_fixer=sql_fixer)
        middleware = (
            *build_middleware(
                summarizer_model=summarizer_model,
                retry_client=retry_client,
                summarization_trigger_tokens=settings.analysis_summarization_trigger_tokens,
                summarization_keep_messages=settings.analysis_summarization_keep_messages,
                model_call_limit=settings.analysis_model_call_limit,
            ),
            trace_tool_call,
        )
        return build_analysis_agent(
            model=analysis_model,
            tools=tools,
            middleware=middleware,
            system_prompt=system_prompt,
            output_schema=output_schema,
        )

    return build_harness
