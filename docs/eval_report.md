# Evaluation report

Generated 2026-10-14T10:14:13+00:00 with provider `offline` against the current catalog build.

## Summary

| Metric | Value |
|---|---|
| End-to-end pass rate | **28/29 (97%)** |
| Policy leaks (table shown that the role is not granted right now) | **0** |
| Label violations incl. forbidden columns (expected-hidden items shown) | **0** |
| Category `access` | 3/3 |
| Category `column_access` | 2/2 |
| Category `lookup` | 12/13 |
| Category `no_match` | 2/2 |
| Category `synonym` | 9/9 |

## Retrieval setting comparison

Measured on questions with an expected table.

| Setting | hit@1 | hit@3 | MRR |
|---|---|---|---|
| FTS5 BM25 only | 0.909 | 0.909 | 0.909 |
| FTS5 BM25 + glossary expansion (default) | 0.955 | 1.0 | 0.977 |

## Per question

| id | role | category | question | expected | top answer | status | rank | AI validation | pass |
|---|---|---|---|---|---|---|---|---|---|
| q01 | analyst | lookup | Where can I find monthly customer churn by region? | analytics.customer_churn_monthly | analytics.customer_churn_monthly | answered | 1 | not_called | PASS |
| q02 | analyst | synonym | Which table has customer attrition numbers? | analytics.customer_churn_monthly | analytics.customer_churn_monthly | answered | 1 | not_called | PASS |
| q03 | analyst | lookup | I need order line items with quantity and discount | sales.order_items | sales.order_items | answered | 1 | not_called | PASS |
| q04 | finance_analyst | lookup | Where is the official monthly revenue by region? | finance.revenue_monthly | finance.revenue_monthly | answered | 1 | not_called | PASS |
| q05 | analyst | access | Where is the official monthly revenue by region? | none (must not leak: finance.revenue_monthly) | analytics.customer_churn_monthly | answered | - | not_called | PASS |
| q06 | hr_partner | lookup | employee salaries by department | hr.employees | hr.employees | answered | 1 | not_called | PASS |
| q07 | analyst | access | employee salaries by department | none (must not leak: hr.employees, hr.payroll) | - | no_match | - | not_called | PASS |
| q08 | hr_partner | synonym | staff headcount per department | hr.employees | hr.employees | answered | 1 | not_called | PASS |
| q09 | admin | lookup | customer email addresses | sales.customers | sales.customers | answered | 1 | not_called | PASS |
| q10 | analyst | column_access | customer email addresses | none (must not leak: sales.customers.email, sales.customers.phone, hr.employees.work_email) | sales.customers | answered | - | not_called | PASS |
| q11 | analyst | synonym | support complaints by priority | support.tickets | support.tickets | answered | 1 | not_called | PASS |
| q12 | viewer | lookup | how satisfied are customers with support? CSAT score | support.csat_responses | support.csat_responses | answered | 1 | not_called | PASS |
| q13 | viewer | lookup | which marketing campaigns spent the most budget | marketing.campaigns | marketing.campaigns | answered | 1 | not_called | PASS |
| q14 | analyst | synonym | website visits that converted from a campaign | marketing.web_sessions | marketing.web_sessions | answered | 1 | not_called | PASS |
| q15 | finance_analyst | lookup | unpaid invoices and their due dates | finance.invoices | finance.invoices | answered | 1 | not_called | PASS |
| q16 | finance_analyst | lookup | payments received by payment method | finance.payments | finance.payments | answered | 1 | not_called | PASS |
| q17 | viewer | lookup | product list price and launch date | product.products | product.products | answered | 1 | not_called | PASS |
| q18 | hr_partner | lookup | gross pay and net pay per pay period | hr.payroll | hr.payroll | answered | 1 | not_called | PASS |
| q19 | hr_partner | column_access | bank account details of employees | none (must not leak: hr.payroll.bank_account_last4) | hr.employees | low_confidence | - | not_called | PASS |
| q20 | analyst | no_match | weather forecast for tomorrow | none | - | no_match | - | not_called | PASS |
| q21 | viewer | no_match | quarterly stock price of competitors | none | product.products | low_confidence | - | not_called | PASS |
| q22 | admin | lookup | daily revenue and average order value by channel | analytics.daily_sales_summary | analytics.daily_sales_summary | answered | 1 | not_called | PASS |
| q23 | analyst | access | daily revenue and average order value by channel | none (must not leak: analytics.daily_sales_summary) | sales.orders | answered | - | not_called | PASS |
| q24 | analyst | lookup | which customers bought which products | sales.order_items, sales.orders | product.products | low_confidence | 2 | not_called | FAIL |
| q25 | analyst | synonym | client attrition figures | analytics.customer_churn_monthly | analytics.customer_churn_monthly | answered | 1 | not_called | PASS |
| q26 | hr_partner | synonym | workforce per department | hr.employees | hr.employees | answered | 1 | not_called | PASS |
| q27 | hr_partner | synonym | wages for each pay period | hr.payroll | hr.payroll | answered | 1 | not_called | PASS |
| q28 | analyst | synonym | helpdesk issues by priority | support.tickets | support.tickets | answered | 1 | not_called | PASS |
| q29 | finance_analyst | synonym | bills that are past due | finance.invoices | finance.invoices | answered | 1 | not_called | PASS |
