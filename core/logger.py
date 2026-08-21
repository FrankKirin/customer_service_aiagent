import structlog

structlog.configure(

)


logger = structlog.get_logger()


if __name__ == "__main__":
    logger.info("logger配置完成")
