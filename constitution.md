# 🏛️ Project Constitution

This document contains the non-negotiable rules for our project. All AI-generated plans, tasks, and code MUST adhere to these principles.

---

## 1. 🥇 General Principles

* **Clarity Over Cleverness:** Code must be easy to read and understand. A junior analyst should be able to understand the "why." Use clear variable names (e.g., `user_revenue_df` instead of `df2`).
* **Write Tests:** All features must have at least one "happy path" test. For data, this means a simple `assert` statement (e.g., `assert df['id'].is_unique`, `assert df['age'].min() >= 18`).
* **No Magic Numbers:** Do not use hard-coded numbers in the logic. Define them as constants at the top of the file (e.g., `SALES_TAX_RATE = 0.08`).
* **Coherence:** ALWAYS make sure that new specs or code is consistant with all other features described in the @PRD.md
* **Efficiency and compatibility:** The application is deployed on a Google Cloud Platform (GCP) and runs on a Google Cloud Run (GCR) + GCS instance. It MUST be efficient and compatible with this GCP environment.

---

## 2. 🐍 Python Rules

* **Always Use Pandas/NumPy for Data:** For any data manipulation (filtering, aggregation), use vectorized pandas or NumPy operations. **Avoid `for` loops** to iterate over DataFrame rows.
* **Use Type Hints:** All new functions MUST have type hints for their arguments and return value.
    * **Good:** `def get_user_data(user_id: int) -> pd.DataFrame:`
    * **Bad:** `def get_user_data(user_id):`
* **Handle Missing Data Explicitly:** Do not let `NaN` values exist in a final dataset. You must explicitly `.fillna()` or `.dropna()` and state in a comment *why* you did.

---

## 3. 🗄️ SQL Rules

* **Never Use `SELECT *`:** In any query that will be used in the app, you MUST specify the exact column names you need (e.g., `SELECT user_id, name, email FROM users`). This prevents the app from breaking if the database schema changes.
* **Filter Data in the Database:** Always use a `WHERE` clause in your SQL query to fetch *only* the data you need. Do not `SELECT` an entire 10-million-row table and then filter it in pandas if you can avoid it.
* **Capitalize SQL Keywords:** For readability, all SQL keywords (`SELECT`, `FROM`, `WHERE`, `GROUP BY`, `JOIN`) MUST be in uppercase.

---

## 4. 🎈 Streamlit Rules

* **Cache Your Data Functions:** Any function that loads data (from a file, API, or SQL database) MUST use Streamlit's caching decorator. This is the #1 rule for performance.
    * **Good:** `@st.cache_data`
* **Use Widgets as Variables:** All Streamlit widgets (like `st.slider` or `st.selectbox`) MUST be assigned to a variable.
* **Keep Layout Simple:** Use `st.container` and `st.columns` to organize the layout. Avoid complex, custom HTML/CSS.
