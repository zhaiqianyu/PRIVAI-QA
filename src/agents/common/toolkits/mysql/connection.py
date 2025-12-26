import concurrent.futures
import threading
import time
from contextlib import contextmanager
from typing import Any

import pymysql
from pymysql import MySQLError
from pymysql.cursors import DictCursor

from src.utils import logger


class MySQLConnectionManager:
    """MySQL 数据库连接管理器"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._local = threading.local()
        self._lock = threading.Lock()
        self.max_connection_age = 3600  # 1小时后重新连接

    def _get_local_connection(self) -> pymysql.Connection | None:
        return getattr(self._local, "connection", None)

    def _set_local_connection(self, connection: pymysql.Connection | None) -> None:
        self._local.connection = connection

    def _get_local_last_time(self) -> float:
        return float(getattr(self._local, "last_connection_time", 0.0) or 0.0)

    def _set_local_last_time(self, last_time: float) -> None:
        self._local.last_connection_time = last_time

    def _get_connection(self) -> pymysql.Connection:
        """获取数据库连接"""
        current_time = time.time()
        connection = self._get_local_connection()
        last_connection_time = self._get_local_last_time()

        if (
            connection is None
            or not connection.open
            or current_time - last_connection_time > self.max_connection_age
        ):
            with self._lock:
                # double-check inside lock
                connection = self._get_local_connection()
                last_connection_time = self._get_local_last_time()

                if (
                    connection is None
                    or not connection.open
                    or current_time - last_connection_time > self.max_connection_age
                ):
                    if connection and connection.open:
                        try:
                            connection.close()
                        except Exception:
                            pass

                    connection = self._create_connection()
                    self._set_local_connection(connection)
                    self._set_local_last_time(current_time)

        return connection

    def _create_connection(self) -> pymysql.Connection:
        """创建新的数据库连接"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                connection = pymysql.connect(
                    host=self.config["host"],
                    user=self.config["user"],
                    password=self.config["password"],
                    database=self.config["database"],
                    port=self.config["port"],
                    charset=self.config.get("charset", "utf8mb4"),
                    cursorclass=DictCursor,
                    connect_timeout=10,
                    read_timeout=60,
                    write_timeout=30,
                    autocommit=True,
                )
                logger.info(f"MySQL connection established successfully (attempt {attempt + 1})")
                return connection

            except MySQLError as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2**attempt)
                else:
                    logger.error(f"Failed to connect to MySQL after {max_retries} attempts: {e}")
                    raise ConnectionError(f"MySQL connection failed: {e}")

    def test_connection(self) -> bool:
        """测试连接是否有效"""
        try:
            connection = self._get_local_connection()
            if connection and connection.open:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                return True
        except Exception:
            pass
        return False

    def _invalidate_connection(self, connection: pymysql.Connection | None = None):
        """关闭并清理失效的连接"""
        try:
            if connection:
                connection.close()
        except Exception:
            pass
        finally:
            local_connection = self._get_local_connection()
            if connection is None or local_connection is connection:
                self._set_local_connection(None)
                self._set_local_last_time(0.0)

    @contextmanager
    def get_cursor(self):
        """获取数据库游标的上下文管理器"""
        max_retries = 2
        cursor = None
        connection = None
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                connection = self._get_connection()
                cursor = connection.cursor()
                break
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to acquire cursor (attempt {attempt + 1}): {e}")
                self._invalidate_connection(connection)
                cursor = None
                connection = None
                if attempt == max_retries - 1:
                    raise
                time.sleep(1)

        if cursor is None or connection is None:
            raise last_error or ConnectionError("Unable to acquire MySQL cursor")

        try:
            yield cursor
            connection.commit()
        except Exception as e:
            try:
                connection.rollback()
            except Exception:
                pass

            if "MySQL" in str(e) or "connection" in str(e).lower():
                logger.warning(f"MySQL connection error encountered, invalidating connection: {e}")
                self._invalidate_connection(connection)

            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass

    def close(self):
        """关闭数据库连接"""
        connection = self._get_local_connection()
        if connection:
            try:
                connection.close()
            finally:
                self._set_local_connection(None)
                self._set_local_last_time(0.0)
            logger.info("MySQL connection closed")

    def get_connection(self) -> pymysql.Connection:
        """对外暴露的连接获取方法"""
        return self._get_connection()

    def invalidate_connection(self):
        """手动标记连接失效"""
        self._invalidate_connection(self._get_local_connection())

    @property
    def database_name(self) -> str:
        """返回当前配置的数据库名称"""
        return self.config["database"]


class QueryTimeoutError(Exception):
    """查询超时异常"""


class QueryResultTooLargeError(Exception):
    """查询结果过大异常"""


def execute_query_with_timeout(
    conn_manager: MySQLConnectionManager,
    sql: str,
    params: tuple | None = None,
    timeout: int = 10,
):
    """使用线程池实现超时控制，避免信号导致的生成器问题。

    注意：不要在不同线程复用同一个 PyMySQL 连接，否则可能出现
    "Packet sequence number wrong" / "read from closed file" 等错误。
    """

    def query_worker():
        connection = conn_manager.get_connection()
        cursor = connection.cursor(DictCursor)
        try:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            # 线程池线程生命周期不固定，避免遗留连接
            conn_manager._invalidate_connection(connection)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(query_worker)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise QueryTimeoutError(f"Query timeout after {timeout} seconds")


def limit_result_size(result: list, max_chars: int = 10000) -> list:
    """限制结果大小"""
    if not result:
        return result

    result_str = str(result)
    if len(result_str) > max_chars:
        limited_result = []
        current_chars = 0
        for row in result:
            row_str = str(row)
            if current_chars + len(row_str) > max_chars:
                break
            limited_result.append(row)
            current_chars += len(row_str)

        logger.warning(
            f"Query result truncated from {len(result)} to {len(limited_result)} rows due to size limit"
        )
        return limited_result

    return result
