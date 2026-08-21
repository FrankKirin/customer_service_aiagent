import structlog

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer(colors=True)  # 至关重要的一步，必须放最后
    ]
)


logger = structlog.get_logger()

