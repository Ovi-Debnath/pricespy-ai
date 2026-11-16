            if len(clients_data) == 0:
                st.info("No clients found.")

            for i, client_item in enumerate(clients_data):
                st.markdown("---")
                
                expiry = client_item.get("expiry_date", "N/A")
                is_expired = expiry != "N/A" and today_str > expiry

                if is_expired:
                    status_badge = "🔴 Expired (Deactivated)"
                elif client_item.get("approved", True):
                    status_badge = "🟢 Approved & Active"
                else:
                    status_badge = "🟡 Pending Payment"

                st.write(f"**Client #{i+1}** — Status: {status_badge}")
                st.write(f"- **Gmail:** `{client_item['client_email'].split(' [')[0]}`")
                st.write(f"- **Phone Number:** `{client_item.get('phone', 'N/A')}`")
                st.write(f"- **Plan:** `{client_item.get('plan', 'Standard')}`")
                st.write(f"- **Payment Method & TrxID:** `{client_item.get('payment_method', 'N/A')} (Trx: {client_item.get('trx_id', 'N/A')})`")
                st.write(f"- **Created Date:** `{client_item.get('created_at', 'N/A')}` | **Expiry Date:** `{expiry}`")
                
                products = client_item.get('products', [])
                st.write(f"- **Active Tracked Products ({len(products)} links):**")
                for idx, item in enumerate(products):
                    p_name = item.get('product_name', 'Product')
                    p_url = item.get('url', '#')
                    l_pr = item.get('live_price', 0.0)
                    b_pr = item.get('baseline_price', 0.0)
                    st.markdown(f"  {idx+1}. **[{p_name}]({p_url})** — Live: `৳{l_pr:,.2f}` | Baseline: `৳{b_pr:,.2f}`")

                pending_prods = client_item.get('pending_products', [])
                if pending_prods:
                    st.warning(f"⏳ **Pending New Products ({len(pending_prods)} links awaiting approval):**")
                    for p_idx, p_item in enumerate(pending_prods):
                        st.markdown(f"  - [Via {p_item.get('payment_method', 'N/A')} | Trx: {p_item.get('trx_id')}] **{p_item.get('product_name')}** — Baseline: `৳{p_item.get('baseline_price')}`")
                    
                    if st.button(f"Approve Pending New URLs for Client #{i+1}", key=f"app_new_{i}"):
                        with st.spinner("Verifying new URLs before approval..."):
                            verified_new = []
                            for p_item in pending_prods:
                                verified_new.append({
                                    "url": p_item.get('url'),
                                    "product_name": p_item.get('product_name'),
                                    "live_price": p_item.get('live_price'),
                                    "baseline_price": p_item.get('baseline_price')
                                })
                            
                            clients_data[i]['products'].extend(verified_new)
                            clients_data[i]['pending_products'] = []
                            res = update_github_clients_file(clients_data, commit_message=f"Admin approved new URLs for {client_item['client_email']}")
                            if res is True:
                                st.success("✅ New URLs approved and added to active monitoring!")
                                st.rerun()
                            else:
                                st.error(f"Error: {res}")

                col_a, col_b = st.columns(2)

                if not client_item.get("approved", True) or is_expired:
                    if col_a.button(f"Approve / Renew #{i+1}", key=f"app_{i}"):
                        with st.spinner("Cleaning URLs, testing live prices and resetting 30-day expiry..."):
                            verified_list = []
                            link_error = None

                            cleaned_products = []
                            for p_item in products:
                                raw_u = p_item.get('url')
                                clean_u = clean_daraz_url(raw_u)
                                b_price = p_item.get('baseline_price')
                                cur_name = p_item.get('product_name')

                                try:
                                    _, l_price = get_competitor_data(clean_u)
                                    verified_list.append({
                                        "url": clean_u,
                                        "title": cur_name,
                                        "baseline_price": b_price,
                                        "live_price": l_price
                                    })
                                    cleaned_products.append({
                                        "url": clean_u,
                                        "product_name": cur_name,
                                        "live_price": l_price,
                                        "baseline_price": b_price
                                    })
                                except Exception as err:
                                    link_error = f"Failed to verify URL ({clean_u}): {err}"
                                    break

                            if link_error:
                                st.error("❌ Cannot approve! " + str(link_error) + f". Please ask the client or contact support at {contact_gmail}.")
                            else:
                                clients_data[i]["approved"] = True
                                clients_data[i]["products"] = cleaned_products
                                clients_data[i]["created_at"] = datetime.now().strftime("%Y-%m-%d")
                                clients_data[i]["expiry_date"] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                                clients_data[i]["reminder_sent"] = False
                                clients_data[i]["expired_notified"] = False
                                
                                res = update_github_clients_file(clients_data, commit_message=f"Approve/Renew client {client_item['client_email']}")

                                if res is True:
                                    mail_sent, mail_msg = send_activation_email(client_item['client_email'], verified_list)
                                    if mail_sent:
                                        st.success(f"Client #{i+1} approved/renewed for another 30 days and email sent successfully!")
                                    else:
                                        st.warning(f"Client approved, but email failed: {mail_msg}")
                                    st.rerun()
                                else:
                                    st.error(f"Failed to sync with GitHub: {res}")

                if col_b.button(f"Remove Client #{i+1}", key=f"del_{i}"):
                    removed_client = clients_data.pop(i)
                    res = update_github_clients_file(clients_data, commit_message=f"Remove client {removed_client['client_email']}")
                    if res is True:
                        st.warning("Client removed and changes saved to GitHub!")
                        st.rerun()
                    else:
                        st.error(f"Failed to sync with GitHub: {res}")

        except Exception as e:
            st.error(f"Error loading data: {e}")
