# Hybrid demo questions: actual vs expected

**Summary:** 6 questions · key facts 29/32 · routed hybrid 6/6 · max generation requests 3 · verifier clean 6/6

## H1. I am a third-year CS student with a 3.8 GPA; what research grants am I eligible for, and what is the deadline to apply?

**Route:** hybrid (stage: semantic) · **generation requests:** 2 · **key facts:** 16/18 · **verifier:** All 16 figures verified against the evidence.

**UniRAG answer:**

As a third-year Computer Science student with a 9.5 CGPA, you are eligible for several research grants. Please note that per policy, you must apply only while the status is **open** [Doc: Research Grant Policy 2026-27 §2].

### Currently Open Grants
You may apply for the following grants immediately:

| Grant Title | Grant Code | Amount | Deadline |
| :--- | :--- | :--- | :--- |
| AI for Social Good Research Fellowship | GR-AI-01 | ₹1,50,000 | 20 Dec 2026 |
| Undergraduate Machine Learning Research Assistantship | GR-ML-02 | ₹60,000 | 10 Jan 2027 |
| Cybersecurity Research Scholarship | GR-CY-03 | ₹75,000 | 31 Jan 2027 |
| Sustainability & Green Computing Grant | GR-SUS-08 | ₹70,000 | 28 Feb 2027 |

*   **Eligibility Notes:** Your CGPA of 9.5 meets the minimum requirements for all the above (e.g., GR-AI-01 requires ≥ 8.5 [Doc: Research Grant Policy 2026-27 §3]; GR-ML-02 requires ≥ 9.0 [Doc: Research Grant Policy 2026-27 §3]). For **GR-CY-03**, you must have no overdue fee dues on the date of application [Doc: Research Grant Policy 2026-27 §3].

### Upcoming and Closed Grants
*   **Upcoming:** HealthTech Innovation Grant (GR-HC-10, ₹90,000) opens 05 Jan 2027; Summer Research Internship Stipend (GR-SP-14, ₹40,000) opens 01 Dec 2026.
*   **Closed:** Hardware Hackathon Seed Fund (GR-HW-06) and Autonomous Robotics Research Grant (GR-ROB-12) are closed and no longer accepting applications [Doc: Research Grant Policy 2026-27 §2].

**SQL:** `SELECT grant_code, title, amount, application_open, application_deadline, status FROM grants WHERE grant_code = ANY(ARRAY['GR-AI-01', 'GR-CY-03', 'GR-ML-02', 'GR-SUS-08', 'GR-ROB-12', 'GR-SP-14', 'GR-HC-10', 'GR-HW-06']) ORDER BY application_deadline`

**Columns:** grant_code, title, amount, application_open, application_deadline, status

**Expected (from the data generator):**

A 3.8 GPA on the 4-point scale converts to CGPA 9.50 (Handbook §4). Matching the Research Grant Policy rules (CGPA, year, department) against grant status, amounts and deadlines in the Grants Register:
  - **GR-AI-01 AI for Social Good Research Fellowship**: ₹1,50,000, apply by **20 Dec 2026** (open)
  - **GR-ML-02 Undergraduate Machine Learning Research Assistantship**: ₹60,000, apply by **10 Jan 2027** (open)
  - **GR-CY-03 Cybersecurity Research Scholarship**: ₹75,000, apply by **31 Jan 2027** (open) — only if the student has no overdue fee dues
  - **GR-SUS-08 Sustainability & Green Computing Grant**: ₹70,000, apply by **28 Feb 2027** (open)
  - **GR-HC-10 HealthTech Innovation Grant**: ₹90,000, not open yet — applications from 05 Jan 2027, deadline 15 Mar 2027
  - **GR-SP-14 Summer Research Internship Stipend**: ₹40,000, not open yet — applications from 01 Dec 2026, deadline 31 Mar 2027
  - GR-HW-06 Hardware Hackathon Seed Fund: eligible but **closed** (all awards filled)
  - GR-ROB-12 Autonomous Robotics Research Grant: eligible but **closed** (all awards filled)

**Missing facts:** 15 Mar 2027, 31 Mar 2027

## H2. Can student S2026CS003 get the hall ticket for all end-semester exams this term?

**Route:** hybrid (stage: semantic) · **generation requests:** 3 · **key facts:** 2/3 · **verifier:** All 12 figures verified against the evidence.

**UniRAG answer:**

Krishna Kale (S2026CS003), you are eligible to receive the hall ticket for all your end-semester exams.

According to the Examination & Attendance Rules 2026-27, a hall ticket is issued if the examination fee is fully paid and the student meets the minimum attendance requirement of 75% in each course [Doc: Examination & Attendance Rules 2026-27 §5 Hall tickets].

Your status for each course is as follows:

| Course Code | Title | Attendance | Fee Status | Eligibility |
| :--- | :--- | :--- | :--- | :--- |
| CS101 | Engineering Mathematics I | 79.6% | paid | Eligible |
| CS102 | Engineering Physics | 73.5% | paid | **Ineligible** |
| CS103 | Programming in C | 85.6% | paid | Eligible |
| CS104 | Basic Electrical Engineering | 77.2% | paid | Eligible |
| CS105 | Programming Lab I | 77.8% | paid | Eligible |

**Supporting Details:**
*   **Fees:** Your examination fee status is "paid" for all courses [SQL].
*   **Attendance:** You meet the 75% minimum attendance requirement [Doc: Examination & Attendance Rules 2026-27 §2 Minimum attendance] for all courses except **CS102 Engineering Physics**, where your attendance is 73.5% [SQL]. 
*   **Detention:** As your attendance in CS102 is above 65%, you are not detained [Doc: Examination & Attendance Rules 2026-27 §4 Detention], but you do not meet the 75% threshold required to take the exam [Doc: Examination & Attendance Rules 2026-27 §2 Minimum attendance]. Please note that attendance between 65% and 75% may be eligible for condonation, though the evidence does not confirm if you have an approved condonation [Doc: Examination & Attendance Rules 2026-27 §5 Hall tickets].

**SQL:** `SELECT c.course_code, c.title, e.attendance_pct, f.component, (f.amount_due - f.amount_paid) AS outstanding_balance, f.status AS fee_status FROM enrollments AS e JOIN courses AS c ON e.course_id = c.course_id LEFT JOIN fee_ledger AS f ON e.student_id = f.student_id AND f.component = 'exam' WHERE e.student_id = 'S2026CS003' AND e.term = '2026-ODD' LIMIT 200`

**Columns:** course_code, title, attendance_pct, component, outstanding_balance, fee_status

**Expected (from the data generator):**

Not automatically. Krishna Kale has paid the exam fee in full and meets the 75% attendance rule in every course except **CS102 Engineering Physics** (73.5%). Because that is between 65% and 75%, attendance can be condoned with a medical certificate and a condonation fee of ₹500 (Examination & Attendance Rules §3); otherwise the student is detained from that course's exam only.

**Missing facts:** ₹500

## H3. As of 30 November 2026, how much late fine has student S2026CS012 accumulated on overdue fees?

**Route:** hybrid (stage: llm) · **generation requests:** 3 · **key facts:** 3/3 · **verifier:** All 11 figures verified against the evidence.

**UniRAG answer:**

As of 30 November 2026, you have accumulated a total late fine of ₹11,000 on your overdue fees.

The total is calculated by summing the late fines for your individual fee components:

| Component | Late Fine |
| :--- | :--- |
| tuition_inst1 | ₹5,500 |
| exam | ₹5,500 |
| **Total** | **₹11,000** |

**Supporting Details:**
* **tuition_inst1:** The database lists a total late fine of ₹5,500 [SQL]. Per policy, late fines for tuition are capped at ₹5,000 per component [Doc: Fee Payment & Refund Policy 2026-27 §4]. Please note that the database value of ₹5,500 exceeds this cap.
* **exam:** A flat late fine of ₹500 is charged for examination fees [Doc: Fee Payment & Refund Policy 2026-27 §4]. The database lists a total late fine of ₹5,500 [SQL].

Late fines are charged on top of the outstanding balance and must be paid before the component is considered cleared [Doc: Fee Payment & Refund Policy 2026-27 §4]. You may apply for a waiver through the Accounts Office only in cases of documented medical emergencies or bereavement within 30 days [Doc: Fee Payment & Refund Policy 2026-27 §4].

**SQL:** `WITH overdue_fees AS (SELECT component, amount_due, amount_paid, due_date, (CAST('2026-11-30' AS DATE) - due_date) AS days_overdue FROM fee_ledger WHERE student_id = 'S2026CS012' AND status = 'overdue'), fine_calculations AS (SELECT component, amount_due, amount_paid, due_date, days_overdue, CASE WHEN component = 'exam' THEN 500 ELSE LEAST(days_overdue * 100, 5000) END AS late_fine FROM overdue_fees) SELECT component, amount_due, amount_paid, due_date, days_overdue, late_fine, SUM(late_fine) OVER () AS total_late_fine FROM fine_calculations LIMIT 200`

**Columns:** component, amount_due, amount_paid, due_date, days_overdue, late_fine, total_late_fine

**Expected (from the data generator):**

Siddharth Ranade has these overdue items (Fee Payment & Refund Policy §4: ₹100 per day per component, capped at ₹5,000; flat ₹500 for the exam fee):
  - tuition_inst1: balance ₹45,000, due 14 Aug 2026 → 108 days × ₹100 = ₹10,800, capped at ₹5,000 → **₹5,000**
  - exam: balance ₹3,500, due 31 Oct 2026 → flat exam late fee ₹500 → **₹500**
  - **Total late fine: ₹5,500**

## H4. Student S2023ENTC001 wants to add one more 3-credit elective this term. Is that allowed?

**Route:** hybrid (stage: llm) · **generation requests:** 3 · **key facts:** 3/3 · **verifier:** All 9 figures verified against the evidence.

**UniRAG answer:**

No, you are not allowed to add another 3-credit elective this term.

While you meet the credit limit requirements, the add/drop period has already closed.

*   **Credit Limit:** You are eligible for a credit overload of up to 28 credits because your CGPA of 8.82 is ≥ 8.5 [Doc: Course Registration & Credit Policy 2026-27 §3]. With your current 25 credits [SQL], adding 3 credits would bring you to 28, which is within the allowed limit.
*   **Add/Drop Period:** Courses can only be added during the add/drop period, which ended on 10 August 2026 [Doc: Course Registration & Credit Policy 2026-27 §4]. After this period, no course can be added [Doc: Course Registration & Credit Policy 2026-27 §4].

| Metric | Value |
| :--- | :--- |
| Current Credits | 25 [SQL] |
| CGPA | 8.82 [SQL] |
| Add/Drop Status | 10 August 2026 (Closed) [Doc: Course Registration & Credit Policy 2026-27 §4] |

**SQL:** `SELECT SUM(c.credits) AS total_registered_credits, CASE WHEN CAST('2026-11-23' AS DATE) BETWEEN CAST('2026-07-31' AS DATE) AND CAST('2027-01-15' AS DATE) THEN 'Within add/drop period' ELSE 'Outside add/drop period' END AS add_drop_status FROM enrollments AS e JOIN courses AS c ON e.course_id = c.course_id WHERE e.student_id = 'S2023ENTC001' AND e.term = '2026-ODD' AND e.status = 'enrolled' LIMIT 200`

**Columns:** total_registered_credits, add_drop_status

**Expected (from the data generator):**

Not this term. On credits alone it would be allowed: Varun Sharma (CGPA 8.82) is registered for **25 credits** in 2026-ODD, and with CGPA ≥ 8.5 the limit is 28 credits with HoD approval, so 25 + 3 = 28 fits (Course Registration & Credit Policy §3). But the add/drop period for 2026-ODD ended on **10 Aug 2026** (§4), so no course can be added now; the student can plan it for the next term.

## H5. Is student S2025CS017 eligible for a merit fee concession, and how much would it be?

**Route:** hybrid (stage: semantic) · **generation requests:** 3 · **key facts:** 3/3 · **verifier:** All 11 figures verified against the evidence.

**UniRAG answer:**

Yes, you are eligible for a merit fee concession. Based on your CGPA of 8.6, you qualify for a 15% concession on your annual tuition fee [Doc: Scholarship & Fee Concession Policy 2026-27 §2].

**Calculation:**
*   **Annual Tuition Fee:** ₹1,45,000 [SQL]
*   **Concession Percentage:** 15% (for CGPA 8.50 – 8.99) [Doc: Scholarship & Fee Concession Policy 2026-27 §2]
*   **Total Concession Amount:** 15% × ₹1,45,000 = ₹21,750

**Eligibility Details:**
*   **CGPA:** Your CGPA of 8.6 meets the requirement of 8.50 or higher [Doc: Scholarship & Fee Concession Policy 2026-27 §2].
*   **Dues:** You have no overdue entries in the fee ledger, satisfying the eligibility condition [Doc: Scholarship & Fee Concession Policy 2026-27 §3].

Please note that this concession is credited against the next academic year's tuition and is split proportionally across the two instalments [Doc: Scholarship & Fee Concession Policy 2026-27 §4].

**SQL:** `SELECT s.full_name, s.cgpa, fl.component, fl.amount_due - fl.amount_paid AS outstanding_balance, fl.status, fs.amount AS annual_tuition_fee FROM students AS s LEFT JOIN fee_ledger AS fl ON s.student_id = fl.student_id AND fl.status = 'overdue' JOIN departments AS d ON s.dept_id = d.dept_id JOIN fee_structure AS fs ON s.dept_id = fs.dept_id AND s.year_of_study = fs.year_of_study WHERE s.student_id = 'S2025CS017' AND d.code = 'CS' AND fs.component = 'tuition' AND fs.academic_year = '2026-27' LIMIT 200`

**Columns:** full_name, cgpa, component, outstanding_balance, status, annual_tuition_fee

**Expected (from the data generator):**

Yes. Riya Shah has CGPA 8.60 (slab 8.50–8.99 → 15%) and no overdue fees (Scholarship & Fee Concession Policy §2–§3). The concession is 15% of the annual tuition of ₹1,45,000 in the 2026-27 fee structure for CS Year 2 = **₹21,750**, applied to next year's tuition.

## H6. Can student S2025CS003 renew their hostel room for next year?

**Route:** hybrid (stage: llm) · **generation requests:** 3 · **key facts:** 2/2 · **verifier:** All 6 figures verified against the evidence.

**UniRAG answer:**

No, you are currently not eligible to renew your hostel room for the next academic year.

According to the **Hostel Rules & Allotment Policy 2026-27 §3**, renewal requires that a student has no overdue fee dues of any kind. Additionally, the **Fee Payment & Refund Policy 2026-27 §5** states that a student with any overdue entry in the fee ledger is not eligible for hostel renewal.

Your eligibility status is as follows:

*   **CGPA:** 7.45 (Requirement: ≥ 6.0) — **Pass** [Doc: Hostel Rules & Allotment Policy 2026-27 §3]
*   **Overdue Dues:** You have an outstanding balance of ₹87,000 [SQL] — **Fail** [Doc: Hostel Rules & Allotment Policy 2026-27 §3]

**Details of overdue dues:**

| Component | Amount Due | Due Date | Status |
| :--- | :--- | :--- | :--- |
| tuition_inst1 | ₹87,000 | 14 Aug 2026 | overdue |

Please note that the renewal window for the next academic year is 1 April to 30 April 2027 [Doc: Hostel Rules & Allotment Policy 2026-27 §3]. You must clear your outstanding balance to become eligible for renewal.

**SQL:** `SELECT s.cgpa, f.txn_id, f.component, f.amount_due, f.amount_paid, (f.amount_due - f.amount_paid) AS outstanding_balance, f.due_date, f.status FROM students AS s JOIN fee_ledger AS f ON s.student_id = f.student_id WHERE s.student_id = 'S2025CS003' AND f.status = 'overdue' LIMIT 200`

**Columns:** cgpa, txn_id, component, amount_due, amount_paid, outstanding_balance, due_date, status

**Expected (from the data generator):**

Not at present. Krishna Iyer meets the academic condition (CGPA 7.45 ≥ 6.0) but has overdue fees: tuition_inst1 (₹87,000 due since 14 Aug 2026). Hostel Rules §3 require no overdue dues for renewal, so the room can be renewed once these are cleared before the renewal window closes.
